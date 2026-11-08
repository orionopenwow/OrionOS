"""The PC side of the phone pair (Orion on Android, Stage 1): one home screen, drafts, the inbox mirror,
the hot mic switch and agent controls, on the lab's phone port (9121) next to phone_api.py.

Routes (every one needs a request signed by a paired phone, see phones.py):

  GET  /phone/home                         read key   everything the phone's home screen shows (cached 3 s)
  POST /phone/draft                        read key   a reply drafted in your style; the PC never sends it
  POST /phone/inbox                        read key   only when "Mirror inbox to my PC" is on here (off by default)
  POST /phone/hotmic   {"state":"on|off"}  off: read key; on: sign key (fingerprint), read key gets 403
  GET  /phone/hotmic                       read key   {"listening": bool}
  POST /phone/agent/{id}/{pause|stop|resume}  sign key; agents Orion did not start are refused with the reason
  POST /phone/agent    {"id","action":"open|pause|stop"}  read key (doing less needs no fingerprint)

Health levels on the phone: 0 everything fine, 1 mostly fine, 2 something needs attention, 3 Orion is frozen.

The inbox mirror is a PC-side setting only (the phone can't turn it on): `python phone_pair.py mirror on|off`,
or GET/POST /phone-pair/settings on the local daemon port. Mirrored messages land in the comms hub as
read-only "phone" threads (replies go from the phone, after your tap) and are deleted after 7 days.

Pocket Orion (os/phone/pocket) reuses this module with a much smaller daemon: everything here reads the
daemon through getattr and copes with missing parts.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import sys
import time
from pathlib import Path
from typing import Any, Callable, Optional

from fastapi import Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

log = logging.getLogger("orion.phone_pair")

HOME_CACHE_S = 3.0
DRAFT_BODY_MAX = 16 * 1024
DRAFT_IN_MAX = 4000          # characters of the message we draft a reply to
DRAFT_OUT_MAX = 1500
CONTEXT_LINES = 8
INBOX_BODY_MAX = 256 * 1024
INBOX_MAX_ITEMS = 100
INBOX_TEXT_MAX = 2000
HOTMIC_BODY_MAX = 1024
RETENTION_S = 7 * 86400
PRUNE_EVERY_S = 3600.0
INBOX_PLATFORM = "phone"
INBOX_ROUTE = "phone_mirror"
HIDDEN = "[hidden]"
DEFAULTS: dict[str, Any] = {"inbox_mirror": False}
MIRROR_OFF = "Inbox mirror is off. Turn on \"Mirror inbox to my PC\" in Orion's settings on your PC first."
READ_ONLY = "Phone messages are read-only on the PC. Reply from your phone."
AGENT_ACTIONS = ("pause", "stop", "resume")
SIMPLE_AGENT_ACTIONS = ("open", "pause", "stop")   # POST /phone/agent (read key)
HIGH_RISK_KINDS = {"boardroom", "self_evolve", "grimoire_run", "skill_step", "devlog", "get_apps"}

_KV = re.compile(r"(?i)\b(pass(?:word)?|passwd|secret|token|api[_-]?key|apikey|bearer|private[_-]?key|pin)\b"
                 r"(\s*(?:is|:|=)\s*)(\S+)")
_SAFE_ID = re.compile(r"[^\w.:@+-]")


# ===================================================================================== secrets
def _looks_secret(text: str) -> bool:
    try:
        from harvester import looks_secret
    except Exception:   # a trimmed install (Pocket Orion) without the harvester: hide long random tokens only
        return bool(re.search(r"[A-Za-z0-9+/_\-]{32,}", text))
    return looks_secret(text)


def redact(text: str) -> str:
    """Hide anything that looks like a password, key or token, keeping the rest of the words."""
    text = str(text or "")
    if not text or not _looks_secret(text):
        return text
    text = _KV.sub(lambda m: m.group(1) + m.group(2) + HIDDEN, text)
    out = []
    for tok in re.split(r"(\s+)", text):
        core = tok.strip(".,:;!?()[]\"'")
        if tok and not tok.isspace() and core != HIDDEN.strip("[]") and tok != HIDDEN \
                and not core.isalpha() and _looks_secret(tok):
            tok = HIDDEN
        out.append(tok)
    return "".join(out)


# ===================================================================================== PC readings
def _uptime_s() -> Optional[int]:
    try:
        return int(float(Path("/proc/uptime").read_text().split()[0]))
    except (OSError, ValueError, IndexError):
        pass
    if sys.platform == "win32":
        try:
            import ctypes
            return int(ctypes.windll.kernel32.GetTickCount64() // 1000)  # type: ignore[attr-defined]
        except Exception:
            return None
    try:
        import psutil  # type: ignore
        return int(time.time() - psutil.boot_time())
    except Exception:
        return None


def _cpu_load() -> Optional[float]:
    try:
        return round(min(1.0, os.getloadavg()[0] / max(1, os.cpu_count() or 1)), 2)
    except (AttributeError, OSError):
        return None


def _battery() -> Optional[int]:
    try:
        from core_signals import read_battery
        pct, _ = read_battery()
    except Exception:
        return None
    return None if pct is None else int(round(pct))


def _pc_name() -> str:
    """[review-fix 8] "Orion PC" or the name set in the Phone panel; never the hostname (it often holds a name)."""
    try:
        import pc_identity
        return pc_identity.pc_name()
    except Exception:
        return "Orion PC"


# ===================================================================================== the pair
class PhonePair:
    def __init__(self, daemon: Any, data_dir: Optional[Path] = None, clock: Callable[[], float] = time.time,
                 name: Optional[str] = None) -> None:
        self.d = daemon
        if data_dir is None:
            from config import settings
            data_dir = settings.data_dir
        self.data_dir = Path(data_dir)
        self.cfg_path = self.data_dir / "phone_pair.json"
        self.clock = clock
        self.name = name                      # shown as the PC's name; None = pc_identity.pc_name()
        self.started = clock()
        self._home: Optional[dict[str, Any]] = None
        self._home_at = -1e9
        self._pruned_at = -1e9

    # ------------------------------------------------------------------ settings (PC side only)
    def settings(self) -> dict[str, Any]:
        try:
            data = json.loads(self.cfg_path.read_text(encoding="utf-8"))
            return {**DEFAULTS, **{k: data[k] for k in DEFAULTS if k in data}}
        except (OSError, ValueError, TypeError):
            return dict(DEFAULTS)

    def set_inbox_mirror(self, on: bool) -> dict[str, Any]:
        cfg = {**self.settings(), "inbox_mirror": bool(on)}
        self.data_dir.mkdir(parents=True, exist_ok=True)
        tmp = self.cfg_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(cfg), encoding="utf-8")
        tmp.replace(self.cfg_path)
        if not on:
            self.prune_inbox(force=True)
        return cfg

    # ------------------------------------------------------------------ home
    def home(self) -> dict[str, Any]:
        now = self.clock()
        if self._home is not None and now - self._home_at < HOME_CACHE_S:
            return self._home
        if now - self._pruned_at >= PRUNE_EVERY_S:
            self.prune_inbox()
        approvals = self._approvals()
        health = self._health()
        snap = self._snapshot()
        frozen = self._frozen()
        self._home = {
            "core": self._core(snap, approvals, health),
            "approvals": approvals,
            "agents": self._agents(),
            "health": health,
            "frozen": frozen,
            "pc": self._pc(snap),
            "missions": self._missions(),   # Mission Control robots with their cost (phone_missions.py)
        }
        self._home_at = now
        return self._home

    @staticmethod
    def _snapshot() -> dict[str, Any]:
        try:
            import core_signals
            inst = getattr(core_signals, "_instance", None)
            return dict(inst.snapshot) if inst is not None and inst.snapshot else {}
        except Exception:
            return {}

    def _core(self, snap: dict[str, Any], approvals: list[dict[str, Any]], health: dict[str, Any]) -> dict[str, Any]:
        hm = getattr(self.d, "hotmic", None)
        try:
            voice = bool(hm.listening) if hm is not None else False
        except Exception:
            voice = False
        lock = getattr(getattr(self.d, "tts", None), "_lock", None)
        try:
            speaking = bool(lock.locked()) if lock is not None else False
        except Exception:
            speaking = False
        return {
            "voice": voice,
            "speaking": speaking,
            "thinking": float(snap.get("thinking") or 0.0),
            "heat": float(snap.get("heat") or 0.0),
            "messages": self._unread(),
            "approvals": len(approvals),
            "health": health["level"],
            "quiet": bool((snap.get("quiet") or 0) >= 0.5),
            "camera": bool(snap.get("camera")),  # Phase 5: presence camera is on (presence.py)
        }

    def _unread(self) -> int:
        hub = getattr(self.d, "comms", None)
        try:
            return int(sum(int(t.get("unread") or 0) for t in hub.threads.values())) if hub is not None else 0
        except Exception:
            return 0

    def _approvals(self) -> list[dict[str, Any]]:
        q = getattr(self.d, "approvals", None)
        try:
            items = list(q.pending()) if q is not None else []
        except Exception:
            items = []
        out = []
        for it in items[:50]:
            kind = str(it.get("kind") or "approval")[:40]
            title = str(it.get("title") or it.get("channel") or it.get("author") or kind.replace("_", " "))
            title = " ".join(title.split())[:160]
            if _looks_secret(title):   # titles only, never the contents; and never anything secret-looking
                title = "Needs your OK (details on your PC)"
            risk = str(it.get("risk") or "").lower()
            if risk not in ("low", "medium", "high"):
                risk = "high" if it.get("category") == "urgent" or kind in HIGH_RISK_KINDS else "medium"
            out.append({"id": str(it.get("id", ""))[:64], "title": title, "risk": risk, "kind": kind})
        return out

    def _agents(self) -> list[dict[str, Any]]:
        tk = getattr(self.d, "agent_toolkit", None)
        agents: list[Any] = []
        try:
            if tk is not None and hasattr(tk, "agents"):
                agents = tk.agents()
            elif tk is not None and getattr(tk, "swarm", None) is not None:
                agents = tk.swarm.list(decorate=False)["agents"]
        except Exception:
            agents = []
        out = []
        for a in agents[:30]:
            if not isinstance(a, dict):
                continue
            out.append({"id": str(a.get("id", ""))[:120], "name": str(a.get("name") or a.get("tool") or "agent")[:80],
                        "tool": str(a.get("tool") or "")[:40], "status": str(a.get("status") or "")[:120],
                        "state": str(a.get("state") or "")[:20]})
            if isinstance(a.get("cost_usd"), (int, float)) and not isinstance(a.get("cost_usd"), bool):
                out[-1]["cost_usd"] = round(float(a["cost_usd"]), 4)   # robots on the phone show what they cost
        return out

    @staticmethod
    def _missions() -> list[dict[str, Any]]:
        try:
            import phone_missions
            return phone_missions.for_home()
        except Exception:   # Pocket Orion and older PCs: no Mission Control
            return []

    def _health(self) -> dict[str, Any]:
        if self._frozen():
            return {"level": 3, "text": "Orion is frozen. Nothing changes until you unfreeze it."}
        mon = getattr(self.d, "health", None)
        last = getattr(mon, "last", None) or {}
        status = str(last.get("status") or "ok")
        level = {"ok": 0, "warn": 1, "fail": 2}.get(status, 0)
        text = " ".join(str(last.get("words") or ("Everything looks fine." if mon is None else "Not checked yet.")).split())
        return {"level": level, "text": redact(text)[:200]}

    @staticmethod
    def _frozen() -> bool:
        try:
            import freeze
            return freeze.is_frozen()
        except Exception:
            return False

    def _pc(self, snap: dict[str, Any]) -> dict[str, Any]:
        cpu = snap.get("cpu")
        cpu = round(float(cpu), 2) if isinstance(cpu, (int, float)) else _cpu_load()
        bat = snap.get("battery")
        bat = int(bat) if isinstance(bat, (int, float)) and not isinstance(bat, bool) else _battery()
        up = _uptime_s()
        return {"name": (self.name or _pc_name())[:64], "online": True, "cpu": cpu, "battery": bat,
                "uptime_s": int(up if up is not None else self.clock() - self.started)}

    # ------------------------------------------------------------------ drafts
    async def draft(self, app: str, sender: str, text: str, thread: Any = None) -> str:
        app = " ".join(str(app or "messages").split())[:40]
        sender = " ".join(str(sender or "someone").split())[:120]
        text = redact(str(text or "").strip()[:DRAFT_IN_MAX])
        context = self._context(app, thread)
        out = ""
        drafter = getattr(self.d, "drafter", None)
        if drafter is not None:
            try:
                from alignment_drafter import ResponseDrafter
                local_first = ResponseDrafter(_LocalFirst(getattr(drafter, "orchestrator", None)),
                                              getattr(drafter, "memory", None))
                res = await local_first.draft(platform=_platform(app), category="dm", channel=app, author=sender,
                                              text=text, thread_context=context)
                out = str(res.get("draft") or "")
            except Exception as exc:
                log.info("Phone draft: no model (%s); using a plain reply", exc)
        if not out.strip():
            out = self._plain(text)
        return redact(out.strip())[:DRAFT_OUT_MAX]

    def _context(self, app: str, thread: Any) -> list[str]:
        if isinstance(thread, list):
            return [redact(str(x))[:400] for x in thread if str(x).strip()][-CONTEXT_LINES:]
        hub = getattr(self.d, "comms", None)
        if hub is None or not thread:
            return []
        try:
            return [redact(x) for x in hub.context_for(f"{INBOX_PLATFORM}:{_thread_id(app, str(thread))}",
                                                       CONTEXT_LINES)]
        except Exception:
            return []

    def _plain(self, text: str) -> str:
        samples: list[str] = []
        hub = getattr(self.d, "comms", None)
        try:
            samples = hub.style_samples() if hub is not None else []
        except Exception:
            samples = []
        try:
            from alignment_drafter import offline_suggestions, style_profile
            return offline_suggestions(text, style_profile(samples))[0]
        except Exception:
            return "Got it, I'll get back to you soon."

    # ------------------------------------------------------------------ inbox mirror
    async def inbox(self, items: list[Any]) -> int:
        if not self.settings()["inbox_mirror"]:
            raise PermissionError(MIRROR_OFF)
        hub = getattr(self.d, "comms", None)
        if hub is None:
            raise LookupError("The PC's message hub isn't running.")
        self._read_only(hub)
        stored = 0
        for it in items[:INBOX_MAX_ITEMS]:
            if not isinstance(it, dict):
                continue
            app = " ".join(str(it.get("app") or "messages").split())[:40]
            sender = " ".join(str(it.get("sender") or "someone").split())[:120]
            text = redact(str(it.get("text") or "").strip()[:INBOX_TEXT_MAX])
            if not text:
                continue
            thread = " ".join(str(it.get("thread") or sender).split())[:120]
            ts = it.get("ts")
            ext = hashlib.blake2b(f"{app}|{thread}|{sender}|{ts}|{text}".encode(), digest_size=10).hexdigest()
            msg = await hub.ingest(platform=INBOX_PLATFORM, thread_id=_thread_id(app, thread),
                                   thread_name=f"{thread} · {app}", author=sender, text=text, route=INBOX_ROUTE,
                                   timestamp=ts, external_id=ext)
            stored += 1 if msg else 0
        self.prune_inbox(force=True)
        return stored

    def _read_only(self, hub: Any) -> None:
        senders = getattr(hub, "senders", None)
        if isinstance(senders, dict) and INBOX_ROUTE not in senders:
            async def refuse(thread: dict[str, Any], text: str) -> bool:
                raise PermissionError(READ_ONLY)
            hub.register_sender(INBOX_ROUTE, refuse)

    def prune_inbox(self, force: bool = False) -> int:
        """Drop mirrored phone messages older than 7 days (all of them when the mirror is off)."""
        now = self.clock()
        if not force and now - self._pruned_at < PRUNE_EVERY_S:
            return 0
        self._pruned_at = now
        hub = getattr(self.d, "comms", None)
        threads = getattr(hub, "threads", None)
        if not isinstance(threads, dict):
            return 0
        cutoff = now - RETENTION_S if self.settings()["inbox_mirror"] else float("inf")
        removed = 0
        for key in list(threads):
            t = threads[key]
            if t.get("platform") != INBOX_PLATFORM:
                continue
            msgs = t["messages"]
            keep = [m for m in msgs if float(m.get("timestamp") or 0) >= cutoff]
            if len(keep) == len(msgs):
                continue
            removed += len(msgs) - len(keep)
            if keep:
                msgs.clear()
                msgs.extend(keep)
                t["unread"] = min(int(t.get("unread") or 0), sum(1 for m in keep if m.get("direction") == "in"))
            else:
                del threads[key]
        if removed and hasattr(hub, "_compact"):
            hub._compact()   # rewrite the history file so old phone messages are really gone
        return removed

    # ------------------------------------------------------------------ hot mic
    async def hotmic(self, state: str) -> dict[str, Any]:
        hm = getattr(self.d, "hotmic", None)
        if hm is None:
            raise LookupError("The hot mic isn't available on this computer.")
        if state == "on":
            st = await asyncio.to_thread(hm.start)
            _notify("Hot mic on", "Turned on from your phone. Orion is listening for your voice.")
        else:
            st = await asyncio.to_thread(hm.stop, True)
            _notify("Hot mic off", "Turned off from your phone.")
        on = bool(st.get("listening")) if isinstance(st, dict) else state == "on"
        return {"listening": on, "on": on}

    def hotmic_state(self) -> dict[str, Any]:
        hm = getattr(self.d, "hotmic", None)
        try:
            on = bool(hm.listening) if hm is not None else False
        except Exception:
            on = False
        return {"listening": on, "on": on, "available": hm is not None}

    def signed_with_sign_key(self, request: Any, device_id: str, body: bytes) -> bool:
        """After a read-level check passed: was this request signed by the fingerprint (sign) key?"""
        board = getattr(self.d, "board", None)
        try:
            import base64
            from boardroom import signing_message
            from phones import verify_p256
            phone = board.phones._phones()[device_id]
            msg = signing_message(request.method, request.url.path, request.headers.get("x-orion-ts") or "", body)
            return verify_p256(base64.b64decode(phone["sign"]), msg, request.headers.get("x-orion-sig") or "")
        except Exception:
            return False

    # ------------------------------------------------------------------ agents
    async def agent(self, agent_id: str, action: str) -> tuple[bool, str]:
        if action not in AGENT_ACTIONS + ("open",):
            return False, "Use pause, stop or resume."
        if action == "resume":
            try:
                import freeze
                why = freeze.blocked("start agents again")
            except Exception:
                why = None
            if why:
                return False, why
        swarm = getattr(getattr(self.d, "agent_toolkit", None), "swarm", None)
        if swarm is None:
            return False, "Agent controls aren't running on this computer."
        ok, msg = await swarm.act(agent_id, action)
        self._home = None          # the next home screen shows the change
        return bool(ok), str(msg)

    # ------------------------------------------------------------------ phone routes (Boardroom.remote_hooks)
    def phone_hook(self, app: Any, phone_auth: Any, json_body: Any) -> None:
        @app.get("/phone/home")
        async def phone_home(request: Request):
            who, err = await phone_auth(request, "read")
            if err is not None:
                return err
            return await asyncio.to_thread(self.home)

        @app.post("/phone/draft")
        async def phone_draft(request: Request):
            who, err = await phone_auth(request, "read", DRAFT_BODY_MAX)
            if err is not None:
                return err
            data = await json_body(request)
            if data is None or not str(data.get("text") or "").strip():
                return JSONResponse({"error": "Nothing to reply to."}, status_code=400)
            self.phone_draft_at = time.time()  # [autotest] the Test Center sees a draft asked for by the phone
            return {"draft": await self.draft(data.get("app", ""), data.get("sender", ""), data.get("text", ""),
                                              data.get("thread"))}

        @app.post("/phone/inbox")
        async def phone_inbox(request: Request):
            who, err = await phone_auth(request, "read", INBOX_BODY_MAX)
            if err is not None:
                return err
            if not self.settings()["inbox_mirror"]:
                return JSONResponse({"error": MIRROR_OFF}, status_code=403)
            data = await json_body(request)
            if data is None or not isinstance(data.get("items"), list):
                return JSONResponse({"error": "bad request"}, status_code=400)
            try:
                n = await self.inbox(data["items"])
            except PermissionError as exc:
                return JSONResponse({"error": str(exc)}, status_code=403)
            except LookupError as exc:
                return JSONResponse({"error": str(exc)}, status_code=503)
            return {"ok": True, "stored": n}

        @app.post("/phone/hotmic")
        async def phone_hotmic(request: Request):
            if int(request.headers.get("content-length") or 0) > HOTMIC_BODY_MAX:
                return JSONResponse({"error": "request too large"}, status_code=413)
            try:
                state = str(json.loads(await request.body() or b"{}").get("state", "")).lower()
            except (ValueError, AttributeError):
                state = ""
            if state not in ("on", "off"):
                return JSONResponse({"error": "state must be on or off"}, status_code=400)
            # Anyone holding the phone may silence the mic; switching it on needs the fingerprint (sign) key.
            who, err = await phone_auth(request, "read", HOTMIC_BODY_MAX)
            if err is not None:
                return err
            if state == "on" and not self.signed_with_sign_key(request, str(who.get("device_id", "")),
                                                               await request.body()):
                return JSONResponse({"error": "Turning the hot mic on needs your fingerprint."}, status_code=403)
            try:
                return await self.hotmic(state)
            except LookupError as exc:
                return JSONResponse({"error": str(exc)}, status_code=503)
            except RuntimeError as exc:
                return JSONResponse({"error": str(exc)}, status_code=409)

        @app.get("/phone/hotmic")
        async def phone_hotmic_state(request: Request):
            who, err = await phone_auth(request, "read")
            if err is not None:
                return err
            return self.hotmic_state()

        @app.post("/phone/agent")
        async def phone_agent_simple(request: Request):
            # Launcher robots: open / pause / stop only make Orion do less, so the read key is enough.
            # Starting an agent again needs the fingerprint: POST /phone/agent/{id}/resume with the sign key.
            who, err = await phone_auth(request, "read")
            if err is not None:
                return err
            data = await json_body(request)
            agent_id = str((data or {}).get("id") or "")[:200]
            action = str((data or {}).get("action") or "")
            if not agent_id or action not in SIMPLE_AGENT_ACTIONS:
                return JSONResponse({"error": "Send an agent id and open, pause or stop."}, status_code=400)
            ok, msg = await self.agent(agent_id, action)
            if not ok:
                return JSONResponse({"error": msg}, status_code=409)
            return {"ok": True, "message": msg}

        @app.post("/phone/agent/{agent_id}/{action}")
        async def phone_agent(agent_id: str, action: str, request: Request):
            who, err = await phone_auth(request, "sign")
            if err is not None:
                return err
            if action not in AGENT_ACTIONS:
                return JSONResponse({"error": "Use pause, stop or resume."}, status_code=400)
            ok, msg = await self.agent(agent_id[:200], action)
            if not ok:
                return JSONResponse({"error": msg}, status_code=409)
            return {"ok": True, "message": msg}


# ===================================================================================== helpers
class _LocalFirst:
    """Wraps the orchestrator so drafts try the local model (Ollama) before any cloud model."""

    def __init__(self, orch: Any) -> None:
        self.orch = orch

    async def frontier_text(self, system: str, user: str, **kw: Any) -> tuple[str, str]:
        orch = self.orch
        if orch is None:
            raise RuntimeError("no model")
        try:
            if await orch.ollama_available():
                body = {"model": orch.cfg.ollama_model, "system": system, "prompt": user, "stream": False,
                        "options": {"temperature": kw.get("temperature", 0.4)}}
                r = await orch.http.post(f"{orch.cfg.ollama_url}/api/generate", json=body, timeout=120.0)
                r.raise_for_status()
                text = str(r.json().get("response") or "")
                if text.strip():
                    return text, orch.cfg.ollama_model
        except Exception as exc:
            log.debug("local draft failed: %s", exc)
        return await orch.frontier_text(system, user, **kw)


def _platform(app: str) -> str:
    a = app.lower()
    for p in ("discord", "telegram", "messenger"):
        if p in a:
            return p
    return "messenger" if "facebook" in a else a or "messages"


def _thread_id(app: str, thread: str) -> str:
    return _SAFE_ID.sub("_", f"{app}:{thread}".lower())[:128]


def _notify(title: str, body: str) -> None:
    try:
        from os_automation import show_os_notification
        show_os_notification(title, body)
    except Exception:
        pass


# ===================================================================================== wiring
class MirrorBody(BaseModel):
    inbox_mirror: bool


_pairs: dict[int, PhonePair] = {}


def attach(daemon: Any, data_dir: Optional[Path] = None, name: Optional[str] = None) -> PhonePair:
    """Call once from the daemon's __init__: registers the phone routes on the lab's phone port."""
    pair = PhonePair(daemon, data_dir, name=name)
    _pairs.clear()
    _pairs[id(daemon)] = pair
    board = getattr(daemon, "board", None)
    if board is not None and pair.phone_hook not in board.remote_hooks:
        board.remote_hooks.append(pair.phone_hook)
    return pair


def make_router(get_daemon: Callable[[], Any]):
    """Local settings on the daemon port (HUD / Settings app): the phone can never turn the mirror on."""
    from fastapi import APIRouter

    router = APIRouter()

    def _pair() -> PhonePair:
        d = get_daemon()
        p = _pairs.get(id(d))
        return p if p is not None else attach(d)

    @router.get("/phone-pair/settings")
    async def get_settings() -> dict[str, Any]:
        return _pair().settings()

    @router.post("/phone-pair/settings")
    async def set_settings(body: MirrorBody) -> dict[str, Any]:
        return _pair().set_inbox_mirror(body.inbox_mirror)

    return router


def _cli(argv: list[str]) -> int:
    from config import settings
    pair = PhonePair(None, settings.data_dir)
    if len(argv) >= 3 and argv[1] == "mirror" and argv[2] in ("on", "off"):
        pair.set_inbox_mirror(argv[2] == "on")
        print("Mirror inbox to my PC: " + argv[2] + (" (restart Orion to drop old phone messages now)"
                                                     if argv[2] == "off" else ""))
        return 0
    if len(argv) >= 2 and argv[1] in ("status", "mirror"):
        print("Mirror inbox to my PC: " + ("on" if pair.settings()["inbox_mirror"] else "off"))
        return 0
    print("usage: python phone_pair.py mirror on|off | status")
    return 2


if __name__ == "__main__":
    raise SystemExit(_cli(sys.argv))
