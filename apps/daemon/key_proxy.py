"""Key Proxy (toolkit tool 9): agents never hold your real keys.

Agents get a placeholder instead of a key, like ORION_KEY_OPENAI=orion-placeholder-<id>. They point their API base
URL at this small local proxy (127.0.0.1 only), for example

    OPENAI_BASE_URL=http://127.0.0.1:9131/openai/v1     OPENAI_API_KEY=orion-placeholder-...
    ANTHROPIC_BASE_URL=http://127.0.0.1:9131/anthropic  ANTHROPIC_API_KEY=orion-placeholder-...

and the proxy forwards each request to the real provider over HTTPS with the real key added, which it takes from
where Orion already keeps keys (config.settings, loaded from Orion's .env). The real key never leaves Orion's
process: a leaked agent log or transcript holds nothing usable.

Each placeholder belongs to one mission or agent and has:
  * the providers (sites) it may reach: the proxy only ever talks to the fixed upstream of an allowed provider;
  * a spending cap in dollars, worked out with the compute meter's price table from the usage each answer
    reports (the calls are also recorded in the compute meter);
  * an expiry, and it can be revoked at any time.

Every call is logged (who, which provider, model, tokens, estimated cost; never headers or bodies), so "which
agents used my OpenAI key today?" has an answer: GET /key-proxy/usage, or ask Orion in chat.

It is a reverse proxy per provider rather than a CONNECT proxy, so it needs no certificates and can't be used to
reach any other site. Daemon routes (local only): /key-proxy/status, /key-proxy/tokens, /key-proxy/usage.
The proxy itself listens on 127.0.0.1:9131 (ORION_KEY_PROXY_PORT; ORION_KEY_PROXY=0 turns it off).
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import secrets
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, AsyncIterator, Callable, Optional

from fastapi import APIRouter, Request  # Request must be a module name: annotations are strings here
from fastapi.responses import JSONResponse

log = logging.getLogger("orion.key_proxy")

PLACEHOLDER = "orion-placeholder-"
DEFAULT_PORT = 9131
DEFAULT_CAP_USD = 5.0
MAX_CAP_USD = 500.0
DEFAULT_TTL_H = 24.0
BODY_KEEP = 2_000_000          # bytes of an answer kept for reading the usage numbers
HOP = {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te", "trailers",
       "transfer-encoding", "upgrade", "host", "content-length", "accept-encoding"}
AUTH_HEADERS = {"authorization", "x-api-key", "x-goog-api-key", "api-key"}


@dataclass(frozen=True)
class Provider:
    name: str
    label: str
    upstream: str
    auth: str                 # bearer | x-api-key | x-goog-api-key
    settings_attr: str = ""   # config.settings attribute holding the real key
    key_env: str = ""         # or an environment variable (loaded from Orion's .env by config.py)
    env_key: str = ""         # what agents' tools call the key variable


PROVIDERS: dict[str, Provider] = {
    "openai": Provider("openai", "OpenAI", "https://api.openai.com", "bearer", "openai_api_key", "", "OPENAI_API_KEY"),
    "anthropic": Provider("anthropic", "Anthropic (Claude)", "https://api.anthropic.com", "x-api-key",
                          "anthropic_api_key", "", "ANTHROPIC_API_KEY"),
    "gemini": Provider("gemini", "Google Gemini", "https://generativelanguage.googleapis.com", "x-goog-api-key",
                       "gemini_api_key", "", "GEMINI_API_KEY"),
}
ALIASES = {"claude": "anthropic", "google": "gemini", "chatgpt": "openai", "gpt": "openai"}


def settings_key(p: Provider) -> str:
    """The real key, through Orion's existing config accessors. Never logged, never returned by a route."""
    if p.settings_attr:
        from config import settings
        return str(getattr(settings, p.settings_attr, "") or "")
    return os.getenv(p.key_env, "").strip() if p.key_env else ""


def _hash(token: str) -> str:
    return hashlib.sha256(f"orion-key-proxy:{token}".encode()).hexdigest()


def _err(status: int, message: str) -> Any:
    from starlette.responses import JSONResponse
    return JSONResponse({"error": {"message": message, "type": "orion_key_proxy"}}, status_code=status)


# =====================================================================================  usage parsing
def _usage_from_obj(provider: str, d: Any, acc: dict[str, Any]) -> None:
    if not isinstance(d, dict):
        return
    if d.get("model") and not acc.get("model"):
        acc["model"] = str(d["model"])
    if d.get("modelVersion") and not acc.get("model"):
        acc["model"] = str(d["modelVersion"])
    if provider == "anthropic":
        msg = d.get("message") if isinstance(d.get("message"), dict) else None
        if msg:                                    # stream: message_start
            if msg.get("model"):
                acc["model"] = str(msg["model"])
            u = msg.get("usage") or {}
            acc["in"] = max(acc.get("in", 0), int(u.get("input_tokens") or 0) + int(u.get("cache_read_input_tokens")
                                                                                    or 0) + int(u.get("cache_creation_input_tokens") or 0))
            acc["out"] = max(acc.get("out", 0), int(u.get("output_tokens") or 0))
        u = d.get("usage") or {}
        if u:
            ins = int(u.get("input_tokens") or 0) + int(u.get("cache_read_input_tokens") or 0) + \
                int(u.get("cache_creation_input_tokens") or 0)
            acc["in"] = max(acc.get("in", 0), ins)
            acc["out"] = max(acc.get("out", 0), int(u.get("output_tokens") or 0))
    elif provider == "gemini":
        u = d.get("usageMetadata") or {}
        if u:
            acc["in"] = max(acc.get("in", 0), int(u.get("promptTokenCount") or 0))
            acc["out"] = max(acc.get("out", 0), int(u.get("candidatesTokenCount") or 0) +
                             int(u.get("thoughtsTokenCount") or 0))
    else:
        u = d.get("usage") or {}
        if isinstance(u, dict) and u:
            acc["in"] = max(acc.get("in", 0), int(u.get("prompt_tokens") or u.get("input_tokens") or 0))
            acc["out"] = max(acc.get("out", 0), int(u.get("completion_tokens") or u.get("output_tokens") or 0))
        resp = d.get("response")                   # Responses API stream: response.completed
        if isinstance(resp, dict):
            _usage_from_obj(provider, resp, acc)


def parse_usage(provider: str, body: bytes, request_model: str = "") -> dict[str, Any]:
    """{model, in, out} from a JSON answer or a server-sent-events stream."""
    acc: dict[str, Any] = {}
    text = body.decode("utf-8", "replace")
    try:
        data = json.loads(text)
        for d in data if isinstance(data, list) else [data]:   # Gemini can answer a JSON array of chunks
            _usage_from_obj(provider, d, acc)
    except ValueError:
        for line in text.splitlines():
            line = line.strip()
            if not line.startswith("data:"):
                continue
            payload = line[5:].strip()
            if not payload or payload == "[DONE]":
                continue
            try:
                _usage_from_obj(provider, json.loads(payload), acc)
            except ValueError:
                continue
    return {"model": acc.get("model") or request_model or "unknown", "in": int(acc.get("in", 0)),
            "out": int(acc.get("out", 0))}


def request_model(provider: str, path: str, body: bytes) -> str:
    if provider == "gemini":
        m = re.search(r"models/([^/:]+)", path)
        if m:
            return m.group(1)
    try:
        d = json.loads(body.decode("utf-8", "replace")) if body else {}
        return str(d.get("model") or "") if isinstance(d, dict) else ""
    except ValueError:
        return ""


# =====================================================================================  the proxy
class KeyProxy:
    def __init__(self, data_dir: Path, port: Optional[int] = None,
                 key_source: Optional[Callable[[Provider], str]] = None,
                 transport: Any = None, meter: Any = None, clock: Callable[[], float] = time.time,
                 upstreams: Optional[dict[str, str]] = None) -> None:
        self.dir = Path(data_dir)
        self.path = self.dir / "key_proxy.json"
        self.log_path = self.dir / "key_proxy_log.jsonl"
        self.port = int(port if port is not None else os.getenv("ORION_KEY_PROXY_PORT") or DEFAULT_PORT)
        self.key_source = key_source or settings_key
        self.transport = transport
        self.meter = meter
        self.clock = clock
        self.state: dict[str, Any] = {"tokens": {}, "providers": {}}
        try:
            if self.path.exists():
                self.state.update(json.loads(self.path.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            pass
        self.providers = dict(PROVIDERS)
        for name, p in (self.state.get("providers") or {}).items():    # extra providers (OpenAI-compatible etc.)
            try:
                if re.fullmatch(r"[a-z0-9-]{2,30}", name) and str(p["upstream"]).startswith("https://"):
                    self.providers[name] = Provider(name, str(p.get("label") or name), str(p["upstream"]).rstrip("/"),
                                                    str(p.get("auth") or "bearer"), "", str(p.get("key_env") or ""),
                                                    str(p.get("key_env") or ""))
            except (KeyError, TypeError):
                continue
        if upstreams:
            for name, url in upstreams.items():
                p = self.providers[name]
                self.providers[name] = Provider(p.name, p.label, url.rstrip("/"), p.auth, p.settings_attr, p.key_env,
                                                p.env_key)
        self._lock = asyncio.Lock()
        self._server: Any = None
        self._task: Optional[asyncio.Task] = None
        self.running = False
        self.error = ""
        self._app: Any = None

    # ------------------------------------------------------------ storage
    def _save(self) -> None:
        try:
            self.dir.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.state, ensure_ascii=False), encoding="utf-8")
            tmp.replace(self.path)
            try:
                os.chmod(self.path, 0o600)
            except OSError:
                pass
        except OSError as exc:
            log.warning("Could not save Key Proxy state: %s", exc)

    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    # ------------------------------------------------------------ placeholders
    def issue(self, mission: str = "", agent: str = "", providers: Optional[list[str]] = None,
              cap_usd: float = DEFAULT_CAP_USD, ttl_hours: float = DEFAULT_TTL_H) -> dict[str, Any]:
        provs = [ALIASES.get(p.lower(), p.lower()) for p in (providers or ["openai", "anthropic", "gemini"])]
        bad = [p for p in provs if p not in self.providers]
        if bad:
            raise ValueError(f"Unknown provider: {', '.join(bad)}. Known: {', '.join(sorted(self.providers))}.")
        try:
            cap = float(cap_usd)
            ttl = float(ttl_hours)
        except (TypeError, ValueError):
            raise ValueError("cap_usd and ttl_hours must be numbers.")
        if not (0 < cap <= MAX_CAP_USD):
            raise ValueError(f"The spending cap must be between $0 and ${MAX_CAP_USD:.0f}.")
        if not (0 < ttl <= 24 * 30):
            raise ValueError("ttl_hours must be between 0 and 720.")
        secret = secrets.token_hex(16)
        token = PLACEHOLDER + secret
        tid = secret[:8]
        now = self.clock()
        rec = {"id": tid, "hash": _hash(token), "mission": mission[:120], "agent": agent[:80],
               "providers": sorted(set(provs)), "cap_usd": round(cap, 4), "spent_usd": 0.0, "calls": 0,
               "created": now, "expires": now + ttl * 3600, "revoked": False}
        self.state.setdefault("tokens", {})[tid] = rec
        self._save()
        return {**self.public(rec), "token": token, "env": self.env_for(token, rec["providers"]),
                "snippets": self.snippets(token, rec["providers"])}

    def public(self, rec: dict[str, Any]) -> dict[str, Any]:
        now = self.clock()
        state = "revoked" if rec.get("revoked") else "expired" if rec.get("expires", 0) < now else \
            "cap reached" if rec.get("spent_usd", 0) >= rec.get("cap_usd", 0) else "active"
        return {k: rec.get(k) for k in ("id", "mission", "agent", "providers", "cap_usd", "spent_usd", "calls",
                                        "created", "expires", "revoked")} | {"state": state}

    def tokens(self) -> list[dict[str, Any]]:
        return [self.public(r) for r in sorted((self.state.get("tokens") or {}).values(),
                                               key=lambda r: -r.get("created", 0))]

    def revoke(self, tid: str) -> bool:
        rec = (self.state.get("tokens") or {}).get(tid)
        if not rec:
            return False
        rec["revoked"] = True
        self._save()
        return True

    def find(self, token: str) -> Optional[dict[str, Any]]:
        if not token.startswith(PLACEHOLDER):
            return None
        h = _hash(token)
        rec = (self.state.get("tokens") or {}).get(token[len(PLACEHOLDER):][:8])
        if rec and secrets.compare_digest(rec.get("hash", ""), h):
            return rec
        return None

    def env_for(self, token: str, providers: list[str]) -> dict[str, str]:
        b = self.base_url()
        env: dict[str, str] = {}
        for p in providers:
            env[f"ORION_KEY_{p.upper().replace('-', '_')}"] = token
        if "openai" in providers:
            env.update({"OPENAI_API_KEY": token, "OPENAI_BASE_URL": f"{b}/openai/v1"})
        if "anthropic" in providers:
            env.update({"ANTHROPIC_API_KEY": token, "ANTHROPIC_BASE_URL": f"{b}/anthropic"})
        if "gemini" in providers:
            env.update({"GEMINI_API_KEY": token, "GOOGLE_GEMINI_BASE_URL": f"{b}/gemini"})
        for p in providers:
            prov = self.providers.get(p)
            if prov and p not in PROVIDERS and prov.env_key:
                env[prov.env_key] = token
        return env

    def snippets(self, token: str, providers: list[str]) -> dict[str, dict[str, str]]:
        """Settings for the coding agents, with how sure we are that each tool honours them."""
        b = self.base_url()
        out: dict[str, dict[str, str]] = {}
        if "anthropic" in providers:
            out["claude_code"] = {
                "env": f"ANTHROPIC_BASE_URL={b}/anthropic\nANTHROPIC_API_KEY={token}",
                "confidence": "works: Claude Code reads ANTHROPIC_BASE_URL and ANTHROPIC_API_KEY. Only for API-key "
                              "use; a Claude subscription login doesn't use a key, so it doesn't pass through here."}
        if "openai" in providers:
            out["codex"] = {
                "env": f"OPENAI_BASE_URL={b}/openai/v1\nOPENAI_API_KEY={token}",
                "confidence": "likely: recent Codex CLI versions read OPENAI_BASE_URL. If yours doesn't, add a "
                              "provider with this base_url in ~/.codex/config.toml. A ChatGPT sign-in doesn't use "
                              "a key and isn't covered."}
            out["openai_sdk"] = {"env": f"OPENAI_BASE_URL={b}/openai/v1\nOPENAI_API_KEY={token}",
                                 "confidence": "works: the official OpenAI SDKs read both."}
        if "gemini" in providers:
            out["gemini_cli"] = {
                "env": f"GOOGLE_GEMINI_BASE_URL={b}/gemini\nGEMINI_API_KEY={token}",
                "confidence": "not sure: some Gemini CLI versions read GOOGLE_GEMINI_BASE_URL, others don't. "
                              "Check with a test call; a Google sign-in doesn't use a key and isn't covered."}
        return out

    # ------------------------------------------------------------ log and usage
    def _log(self, row: dict[str, Any]) -> None:
        try:
            self.dir.mkdir(parents=True, exist_ok=True)
            with self.log_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
        except OSError as exc:
            log.warning("Could not write the Key Proxy log: %s", exc)

    def usage(self, provider: str = "", since: float = 0.0, agent: str = "", when: str = "") -> dict[str, Any]:
        provider = ALIASES.get(provider.lower(), provider.lower()) if provider else ""
        rows = []
        try:
            with self.log_path.open(encoding="utf-8") as f:
                for line in f:
                    try:
                        r = json.loads(line)
                    except ValueError:
                        continue
                    if r.get("at", 0) < since or (provider and r.get("provider") != provider) or \
                            (agent and agent.lower() not in f"{r.get('agent', '')} {r.get('mission', '')}".lower()):
                        continue
                    rows.append(r)
        except OSError:
            pass
        who: dict[str, dict[str, Any]] = {}
        for r in rows:
            k = r.get("agent") or r.get("mission") or f"placeholder {r.get('token')}"
            w = who.setdefault(k, {"agent": k, "missions": set(), "calls": 0, "cost": 0.0, "tokens_in": 0,
                                   "tokens_out": 0, "refused": 0, "providers": set()})
            w["calls"] += 1
            w["cost"] += float(r.get("cost") or 0)
            w["tokens_in"] += int(r.get("tokens_in") or 0)
            w["tokens_out"] += int(r.get("tokens_out") or 0)
            w["refused"] += 1 if r.get("refused") else 0
            w["providers"].add(r.get("provider"))
            if r.get("mission"):
                w["missions"].add(r["mission"])
        agents = sorted(({**w, "missions": sorted(w["missions"]), "providers": sorted(p for p in w["providers"] if p),
                          "cost": round(w["cost"], 4)} for w in who.values()), key=lambda w: -w["calls"])
        return {"provider": provider, "since": since, "calls": len(rows), "agents": agents,
                "answer": self.answer(agents, provider, since, when)}

    def answer(self, agents: list[dict[str, Any]], provider: str, since: float, when: str = "") -> str:
        p = self.providers.get(provider)
        what = f"your {p.label} key" if p else "your keys"
        when = when or (("since " + time.strftime("%a %d %b %H:%M", time.localtime(since))) if since else "so far")
        used = [a for a in agents if a["calls"] > a["refused"]]
        if not agents:
            return f"No agent used {what} through Orion {when}."
        parts = []
        for a in agents[:8]:
            s = f"{a['agent']}: {a['calls'] - a['refused']} call{'s' if a['calls'] - a['refused'] != 1 else ''}"
            if a["cost"]:
                s += f", about ${a['cost']:.2f}"
            if a["refused"]:
                s += f" ({a['refused']} refused)"
            parts.append(s)
        head = f"{len(used)} agent{'s' if len(used) != 1 else ''} used {what} {when}" if used else \
            f"Nobody got through to {what} {when}, but there were refused attempts"
        return head + ". " + "; ".join(parts) + ". (Costs are estimates.)"

    # ------------------------------------------------------------ HTTP
    def app(self) -> Any:
        if self._app is None:
            from starlette.applications import Starlette
            from starlette.routing import Route
            methods = ["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"]
            self._app = Starlette(routes=[Route("/", self._home, methods=["GET"]),
                                          Route("/{provider}/{path:path}", self.handle, methods=methods),
                                          Route("/{provider}", self.handle, methods=methods)])
        return self._app

    async def _home(self, request: Any) -> Any:
        from starlette.responses import JSONResponse
        return JSONResponse({"orion_key_proxy": True, "providers": sorted(self.providers)})

    @staticmethod
    def _placeholder(request: Any) -> tuple[str, bool]:
        """(placeholder, a non-placeholder key was sent)."""
        found_other = False
        for h in ("authorization", "x-api-key", "x-goog-api-key", "api-key"):
            v = request.headers.get(h)
            if not v:
                continue
            v = v.strip()
            if h == "authorization":
                v = re.sub(r"^(bearer|token)\s+", "", v, flags=re.I)
            if v.startswith(PLACEHOLDER):
                return v, False
            found_other = True
        q = request.query_params.get("key")
        if q:
            if q.startswith(PLACEHOLDER):
                return q, False
            found_other = True
        return "", found_other

    async def handle(self, request: Any) -> Any:
        import httpx
        from starlette.background import BackgroundTask
        from starlette.responses import StreamingResponse

        name = ALIASES.get(request.path_params["provider"].lower(), request.path_params["provider"].lower())
        path = request.path_params.get("path", "")
        if request.headers.get("origin"):
            return _err(403, "Web pages can't use Orion's Key Proxy.")
        prov = self.providers.get(name)
        if prov is None:
            return _err(404, f"Orion's Key Proxy doesn't know “{name}”. Known: {', '.join(sorted(self.providers))}.")
        token, other = self._placeholder(request)
        base = {"at": self.clock(), "provider": name, "method": request.method, "path": "/" + path[:200]}
        if not token:
            self._log({**base, "refused": "no placeholder", "status": 401})
            return _err(401, "Send your Orion placeholder (orion-placeholder-...) as the API key. "
                        + ("Real keys aren't passed through here." if other else ""))
        rec = self.find(token)
        if rec is None:
            self._log({**base, "refused": "unknown placeholder", "status": 401})
            return _err(401, "Orion doesn't know this placeholder.")
        who = {"token": rec["id"], "agent": rec.get("agent", ""), "mission": rec.get("mission", "")}
        now = self.clock()
        refusal = None
        if rec.get("revoked"):
            refusal = (401, "revoked", "This placeholder was revoked in Orion.")
        elif rec.get("expires", 0) < now:
            refusal = (401, "expired", "This placeholder has expired. Ask Orion for a new one.")
        elif name not in rec.get("providers", []):
            refusal = (403, "provider not allowed", f"This placeholder isn't allowed to use {prov.label}.")
        elif rec.get("spent_usd", 0) >= rec.get("cap_usd", 0):
            refusal = (402, "spending cap reached",
                       f"This mission reached its spending cap (${rec.get('cap_usd', 0):.2f}). Raise it in Orion.")
        if refusal:
            self._log({**base, **who, "refused": refusal[1], "status": refusal[0]})
            return _err(refusal[0], refusal[2])
        real = self.key_source(prov)
        if not real:
            self._log({**base, **who, "refused": "no key set up", "status": 503})
            return _err(503, f"No {prov.label} key is set up in Orion yet. Add it in Orion's settings.")

        body = await request.body()
        headers = {k: v for k, v in request.headers.items() if k.lower() not in HOP | AUTH_HEADERS}
        headers["accept-encoding"] = "identity"
        if prov.auth == "bearer":
            headers["authorization"] = f"Bearer {real}"
        else:
            headers[prov.auth] = real
        params = [(k, v) for k, v in request.query_params.multi_items() if k != "key"]
        url = f"{prov.upstream}/{path}"
        model_asked = request_model(name, path, body)
        client = httpx.AsyncClient(transport=self.transport, timeout=httpx.Timeout(600.0, connect=15.0))
        try:
            up = await client.send(client.build_request(request.method, url, params=params, headers=headers,
                                                        content=body), stream=True)
        except httpx.HTTPError as exc:
            await client.aclose()
            self._log({**base, **who, "refused": f"couldn't reach {prov.label}", "status": 502})
            return _err(502, f"Orion couldn't reach {prov.label} ({type(exc).__name__}).")
        kept = bytearray()

        async def relay() -> AsyncIterator[bytes]:
            async for chunk in up.aiter_bytes():
                if len(kept) < BODY_KEEP:
                    kept.extend(chunk[:BODY_KEEP - len(kept)])
                else:                                  # keep the tail, where streams report usage
                    kept[:] = (bytes(kept) + chunk)[-BODY_KEEP:]
                yield chunk

        async def done() -> None:
            await up.aclose()
            await client.aclose()
            await self._account(rec, name, base, who, up.status_code, bytes(kept), model_asked)

        out_headers = {k: v for k, v in up.headers.items()
                       if k.lower() not in HOP | {"content-encoding", "set-cookie"}}
        return StreamingResponse(relay(), status_code=up.status_code, headers=out_headers,
                                 background=BackgroundTask(done))

    async def _account(self, rec: dict[str, Any], provider: str, base: dict[str, Any], who: dict[str, Any],
                       status: int, body: bytes, model_asked: str) -> None:
        u = parse_usage(provider, body, model_asked) if status < 400 else {"model": model_asked, "in": 0, "out": 0}
        cost = 0.0
        try:
            from compute_meter import cost_of, load_prices, price_for
            prices = self.meter.prices() if self.meter is not None else load_prices()
            price, _known = price_for(u["model"], provider if provider in PROVIDERS else "cloud", prices)
            cost = cost_of(u["in"], u["out"], price)
        except Exception:
            log.debug("cost estimate failed", exc_info=True)
        async with self._lock:
            rec["spent_usd"] = round(float(rec.get("spent_usd", 0)) + cost, 6)
            rec["calls"] = int(rec.get("calls", 0)) + 1
            self._save()
        if self.meter is not None and (u["in"] or u["out"]):
            try:
                self.meter.record(u["model"], provider, u["in"], u["out"])
            except Exception:
                log.debug("meter record failed", exc_info=True)
        self._log({**base, **who, "status": status, "model": u["model"], "tokens_in": u["in"],
                   "tokens_out": u["out"], "cost": round(cost, 6)})

    # ------------------------------------------------------------ lifecycle
    async def start(self) -> None:
        if os.getenv("ORION_KEY_PROXY", "1").strip().lower() in ("0", "off", "false", "no"):
            self.error = "turned off (ORION_KEY_PROXY=0)"
            return
        import uvicorn
        cfg = uvicorn.Config(self.app(), host="127.0.0.1", port=self.port, log_level="warning", lifespan="off",
                             access_log=False)
        self._server = uvicorn.Server(cfg)
        self._server.install_signal_handlers = lambda: None   # the daemon owns the signals

        async def serve() -> None:
            try:
                await self._server.serve()
            except (OSError, SystemExit) as exc:
                self.error = f"couldn't listen on port {self.port} ({exc})"
                log.warning("Key Proxy: %s", self.error)
            finally:
                self.running = False
        self._task = asyncio.get_running_loop().create_task(serve(), name="key-proxy")
        for _ in range(50):
            await asyncio.sleep(0.02)
            if getattr(self._server, "started", False) or self._task.done():
                break
        self.running = bool(getattr(self._server, "started", False))

    async def stop(self) -> None:
        if self._server is not None:
            self._server.should_exit = True
        if self._task is not None:
            try:
                await asyncio.wait_for(self._task, 5)
            except (asyncio.TimeoutError, asyncio.CancelledError, Exception):
                self._task.cancel()
            self._task = None
        self.running = False

    def status(self) -> dict[str, Any]:
        provs = []
        for p in self.providers.values():
            try:
                has = bool(self.key_source(p))
            except Exception:
                has = False
            provs.append({"name": p.name, "label": p.label, "upstream": p.upstream, "key_set_up": has,
                          "base_url": f"{self.base_url()}/{p.name}" + ("/v1" if p.name == "openai" else "")})
        return {"running": self.running, "port": self.port, "address": self.base_url(), "error": self.error,
                "providers": provs, "tokens": self.tokens()[:50]}


# =====================================================================================  plain-words helpers
def since_from(period: str, now: Optional[float] = None) -> float:
    now = now or time.time()
    t = time.localtime(now)
    midnight = time.mktime((t.tm_year, t.tm_mon, t.tm_mday, 0, 0, 0, 0, 0, -1))
    p = (period or "today").lower().strip()
    if p in ("today", ""):
        return midnight
    if p == "yesterday":
        return midnight - 86400
    if p in ("week", "this week", "7d"):
        return now - 7 * 86400
    if p in ("24h", "day"):
        return now - 86400
    if p in ("all", "ever"):
        return 0.0
    try:
        return float(p)
    except ValueError:
        return midnight


PERIOD_WORDS = {"today": "today", "yesterday": "since yesterday", "week": "this week", "7d": "this week",
                "24h": "in the last 24 hours", "all": "so far"}
USAGE_QUESTION = re.compile(r"\b(which|what|who)\b.{0,40}\b(agents?|tools?|missions?|bots?)\b.{0,40}\b(use[ds]?|using|"
                            r"touch(ed)?)\b.{0,40}\bkeys?\b|\bwho\b.{0,30}\b(used|using)\b.{0,30}\bkeys?\b", re.I)


def parse_usage_question(text: str) -> Optional[tuple[str, str]]:
    """(provider, period) for questions like "which agents used my OpenAI key today?", else None."""
    if not USAGE_QUESTION.search(text or ""):
        return None
    low = text.lower()
    provider = ""
    for word, p in (("openai", "openai"), ("chatgpt", "openai"), ("gpt", "openai"), ("anthropic", "anthropic"),
                    ("claude", "anthropic"), ("gemini", "gemini"), ("google", "gemini")):
        if re.search(rf"\b{word}\b", low):
            provider = p
            break
    period = "today"
    for word, per in (("yesterday", "yesterday"), ("this week", "week"), ("last week", "week"), ("ever", "all"),
                      ("all time", "all"), ("24 hours", "24h")):
        if word in low:
            period = per
            break
    return provider, period


# =====================================================================================  routes
def make_router(get_proxy: Callable[[], KeyProxy]):

    router = APIRouter(prefix="/key-proxy", tags=["toolkit"])

    @router.get("/status")
    async def status() -> Any:
        return get_proxy().status()

    @router.get("/tokens")
    async def tokens() -> Any:
        return {"tokens": get_proxy().tokens()}

    @router.post("/tokens")
    async def issue(request: Request) -> Any:
        try:
            b = await request.json()
            if not isinstance(b, dict):
                raise ValueError("Send a JSON object.")
            provs = b.get("providers")
            if provs is not None and not (isinstance(provs, list) and all(isinstance(p, str) for p in provs)):
                raise ValueError("providers must be a list of names.")
            return get_proxy().issue(str(b.get("mission") or ""), str(b.get("agent") or ""), provs,
                                     b.get("cap_usd", DEFAULT_CAP_USD), b.get("ttl_hours", DEFAULT_TTL_H))
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)

    @router.post("/tokens/{token_id}/revoke")
    async def revoke(token_id: str) -> Any:
        if not get_proxy().revoke(token_id):
            return JSONResponse({"error": "No placeholder with that id."}, status_code=404)
        return {"revoked": token_id}

    @router.get("/usage")
    async def usage(provider: str = "", since: str = "today", agent: str = "") -> Any:
        kp = get_proxy()
        return kp.usage(provider, since_from(since, kp.clock()), agent, PERIOD_WORDS.get(since, ""))

    return router


# =====================================================================================  MCP tool
def mcp_tools(tools: Any) -> list[Any]:
    from mcp_tools import DaemonUnavailable, Tool, ToolError, _schema

    async def key_proxy_usage(a: dict[str, Any]) -> str:
        q = "?" + "&".join(f"{k}={v}" for k, v in (("provider", a.get("provider") or ""),
                                                     ("since", a.get("since") or "today")) if v)
        try:
            status, data = await tools.backend.call("GET", "/key-proxy/usage" + q)
        except DaemonUnavailable:
            raise ToolError("Orion isn't running, so the Key Proxy log can't be read.")
        if status >= 400:
            raise ToolError(str(data.get("error") or f"Orion refused it ({status})."))
        return tools.out(data.get("answer") or "No calls.")

    return [Tool("key_proxy_usage", "Who used my API keys?",
                 "Key Proxy: which agents and missions called a provider (OpenAI, Anthropic, Gemini) through "
                 "Orion's key proxy, how often, and the estimated cost. Agents only ever hold placeholders; the "
                 "real keys stay inside Orion.", "read",
                 _schema({"provider": {"type": "string", "description": "openai | anthropic | gemini (empty: all)"},
                          "since": {"type": "string", "description": "today (default), yesterday, week, 24h, all"}}),
                 key_proxy_usage)]
