"""Tripwires (toolkit tool 22): know if someone is inside.

Orion plants bait where an intruder or a rogue script would look: an old passwords file, a fake database login in
a .env.backup, a fake cloud key, a note on a VM's desktop, an old git branch. Every bait carries its own unique
token inside a link that goes to Orion's own address and nowhere else (the Canarytokens idea, self-hosted, no
third party). If anything ever *uses* one (opens the "admin panel" link, tries the fake cloud key against its
endpoint), the request lands on /tripwire/<token> and your phone gets an urgent alert: which bait, from which
address, with which program. Until then it is silent.

Safety rules:
  * Bait is built at runtime, never written into the repo. Every value carries the marker "orntw", so Secret
    Shield and gitleaks (allow lists in secret_shield_rules.json and .gitleaks.toml) know it's Orion's own decoy
    and don't block or report it. The fake keys follow the real formats only loosely.
  * Nothing is ever planted inside Orion's own (public) repo unless you say so for that one plant.
  * Only files Orion planted are removed again, and only while they still hold their token.
  * Planting is an approval (sensitive task "safety.tripwires"): "Orion, set tripwires on the server" asks your
    phone first. Removing Orion's own bait doesn't need one.
  * Hits only ever reach Orion: the daemon's own route, or the small listener below when this Orion runs on the
    server (Settings > Tripwires > listener; or `python3 tripwires.py listen --port 9135` without the daemon,
    which keeps the hits for the daemon to pick up). DNS tokens are out of scope.

HTTP (local):
  GET  /tripwires                 state: config, planted bait (no token values), recent hits
  POST /tripwires/plant {target?} asks for approval, then plants the configured bait
  POST /tripwires/remove          removes all of Orion's bait
  POST /tripwires/config {...}    callback address, listener, which bait where
  ANY  /tripwire/<token>/...      the alarm itself
"""

import json
import logging
import os
import re
import secrets
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Optional

log = logging.getLogger("orion.tripwires")

MARKER = "orntw"
TOKEN_RE = re.compile(MARKER + r"[0-9a-f]{11,16}", re.I)
TASK = "safety.tripwires"
HIT_QUIET_S = 600            # one alert per bait per 10 minutes; later hits are counted
MAX_HITS = 200
DEFAULT_LISTENER = {"enabled": False, "bind": "0.0.0.0", "port": 9135}
GIT_BRANCH = "old/deploy-2023"

KINDS: dict[str, dict[str, str]] = {
    "passwords": {"label": "an old passwords file", "path": "~/Documents/passwords-old.txt"},
    "env_backup": {"label": "a fake database login (.env.backup)", "path": "~/backups/site/.env.backup"},
    "cloud_key": {"label": "a fake cloud key", "path": "~/.config/cloud-backup/credentials"},
    "desktop": {"label": "a server-logins note on the desktop", "path": "~/Desktop/server-logins.txt"},
    "git_branch": {"label": "an old git branch with deploy settings", "path": ""},
}
DEFAULT_BAITS = [{"kind": "passwords"}, {"kind": "env_backup"}, {"kind": "cloud_key"}, {"kind": "desktop"}]


def new_token() -> str:
    return MARKER + secrets.token_hex(8)


def _fake_password(token: str) -> str:
    """Looks like a generated password to a person; carries the marker so Secret Shield skips it."""
    return token[:5].capitalize() + token[5:13] + "!" + token[13:]


def bait_text(kind: str, token: str, callback: str) -> tuple[str, list[str]]:
    """(file content, needles that identify the bait if they show up in a request)."""
    url = f"{callback.rstrip('/')}/tripwire/{token}"
    pw = _fake_password(token)
    key_id = "AKIA" + token[:16].upper()
    secret_key = token + secrets.token_hex(12)
    if kind == "passwords":
        text = ("old logins (moved to the password manager, delete this)\n\n"
                f"server admin panel   {url}/admin\n  user: admin\n  pass: {pw}\n\n"
                f"backup storage       {url}/storage\n  user: backup\n  pass: {pw[::-1]}\n")
    elif kind == "env_backup":
        text = ("# backup of production settings, do not commit\n"
                "APP_ENV=production\n"
                "DB_HOST=db-primary\nDB_PORT=3306\nDB_USER=admin\n"
                f"DB_PASSWORD={pw}\n"
                f"DB_ADMIN_URL={url}/phpmyadmin\n"
                f"API_BASE_URL={url}/api\n")
    elif kind == "cloud_key":
        text = ("[default]\n"
                f"aws_access_key_id = {key_id}\n"
                f"aws_secret_access_key = {secret_key}\n"
                "region = eu-west-2\n"
                f"endpoint_url = {url}/s3\n")
    elif kind == "desktop":
        text = ("server logins (temporary)\n\n"
                f"control panel: {url}/login\n"
                f"root / {pw}\n")
    elif kind == "git_branch":
        text = ("# deploy settings for the old host\n"
                f"DEPLOY_URL={url}/deploy\n"
                "DEPLOY_USER=deploy\n"
                f"DEPLOY_PASSWORD={pw}\n"
                f"S3_KEY_ID={key_id}\nS3_SECRET={secret_key}\n")
    else:
        raise ValueError(f"unknown bait kind {kind}")
    return text, [token, key_id[4:]]


class TripwireError(Exception):
    pass


class Tripwires:
    def __init__(self, data_dir: Path, *, home: Optional[Path] = None, repo_root: Optional[Path] = None,
                 default_callback: str = "http://127.0.0.1:9120", notify: Optional[Callable[..., None]] = None,
                 publish: Optional[Callable[[dict[str, Any]], None]] = None, clock: Callable[[], float] = time.time,
                 run: Callable[..., Any] = subprocess.run) -> None:
        self.dir = Path(data_dir)
        self.path = self.dir / "tripwires.json"
        self.hits_inbox = self.dir / "tripwire-hits.jsonl"     # from a listener running without the daemon
        self.home = Path(home) if home else Path.home()
        self.repo_root = Path(repo_root).resolve() if repo_root else None
        self.default_callback = default_callback
        self.notify = notify or _alert
        self.publish = publish
        self.clock = clock
        self.run = run
        self._lock = threading.RLock()
        self._server: Optional[ThreadingHTTPServer] = None

    # ------------------------------------------------------------------ state
    def _load(self) -> dict[str, Any]:
        try:
            st = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(st, dict):
                st.setdefault("config", {})
                st.setdefault("planted", {})
                st.setdefault("hits", [])
                return st
        except (OSError, ValueError):
            pass
        return {"config": {}, "planted": {}, "hits": [], "inbox_offset": 0}

    def _save(self, st: dict[str, Any]) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(st, indent=1), encoding="utf-8")
        os.chmod(tmp, 0o600)
        os.replace(tmp, self.path)

    def config(self) -> dict[str, Any]:
        cfg = self._load()["config"]
        lis = {**DEFAULT_LISTENER, **(cfg.get("listener") or {})}
        return {"callback": str(cfg.get("callback") or self.default_callback),
                "baits": list(cfg.get("baits") or DEFAULT_BAITS),
                "listener": lis, "repo": str(cfg.get("repo") or "")}

    def set_config(self, changes: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            st = self._load()
            cfg = st["config"]
            if "callback" in changes:
                cb = str(changes["callback"] or "").strip().rstrip("/")
                if cb and not re.fullmatch(r"https?://[A-Za-z0-9.\-\[\]:]+(:\d{1,5})?", cb):
                    raise TripwireError("The callback address looks like http://this-server:9135 (Orion's own "
                                        "address, never someone else's).")
                cfg["callback"] = cb
            if "listener" in changes and isinstance(changes["listener"], dict):
                lis = {**DEFAULT_LISTENER, **(cfg.get("listener") or {})}
                new = changes["listener"]
                if "enabled" in new:
                    lis["enabled"] = bool(new["enabled"])
                if "port" in new:
                    port = int(new["port"])
                    if not 1024 <= port <= 65535:
                        raise TripwireError("Pick a port between 1024 and 65535.")
                    lis["port"] = port
                if "bind" in new:
                    lis["bind"] = str(new["bind"] or "0.0.0.0")[:64]
                cfg["listener"] = lis
            if "baits" in changes and isinstance(changes["baits"], list):
                baits = []
                for b in changes["baits"]:
                    if isinstance(b, dict) and b.get("kind") in KINDS:
                        baits.append({"kind": b["kind"], **({"path": str(b["path"])} if b.get("path") else {})})
                cfg["baits"] = baits
            if "repo" in changes:
                cfg["repo"] = str(changes["repo"] or "")
            self._save(st)
        return self.config()

    def public(self) -> dict[str, Any]:
        st = self._load()
        planted = [{"id": t[-6:], "kind": p["kind"], "label": KINDS.get(p["kind"], {}).get("label", p["kind"]),
                    "where": p.get("where", ""), "planted_at": p.get("planted_at"), "hits": p.get("hits", 0),
                    "last_hit": p.get("last_hit")} for t, p in st["planted"].items()]
        hits = [{k: v for k, v in h.items() if k != "token"} for h in st["hits"][-20:]][::-1]
        cfg = self.config()
        return {"config": cfg, "planted": planted, "hits": hits, "armed": bool(planted),
                "listening": self._server is not None, "words": self.words(planted, hits)}

    @staticmethod
    def words(planted: list[dict[str, Any]], hits: list[dict[str, Any]]) -> str:
        if not planted:
            return "No tripwires set. Say \"Orion, set tripwires\" to plant some."
        if hits:
            h = hits[0]
            return (f"{len(planted)} tripwire{'s' if len(planted) != 1 else ''} set. Last one tripped: "
                    f"{h.get('label')} from {h.get('ip')} ({_ago(h.get('at'))}).")
        return f"{len(planted)} tripwire{'s' if len(planted) != 1 else ''} set, none tripped."

    # ------------------------------------------------------------------ planting
    def _resolve(self, raw: str) -> Path:
        p = Path(raw.replace("~", str(self.home), 1) if raw.startswith("~") else raw)
        return p if p.is_absolute() else self.home / p

    def _inside_public_repo(self, p: Path) -> bool:
        if self.repo_root is None:
            return False
        try:
            rp = p.resolve()
        except OSError:
            rp = p
        return rp == self.repo_root or self.repo_root in rp.parents

    def plan(self) -> list[dict[str, str]]:
        """What would be planted where, in words (for the approval card)."""
        cfg = self.config()
        out = []
        for b in cfg["baits"]:
            kind = b["kind"]
            where = cfg["repo"] if kind == "git_branch" else str(b.get("path") or KINDS[kind]["path"])
            if kind == "git_branch" and not where:
                continue
            out.append({"kind": kind, "label": KINDS[kind]["label"], "where": where})
        return out

    def plant(self, allow_public_repo: bool = False) -> dict[str, Any]:
        """Plant every configured bait. Skips (with a reason) anything that isn't safe to plant."""
        cfg = self.config()
        callback = cfg["callback"]
        done, skipped = [], []
        with self._lock:
            st = self._load()
            existing = {(p["kind"], p.get("target")) for p in st["planted"].values()}
            for b in self.plan():
                kind, where = b["kind"], b["where"]
                if (kind, where) in existing:
                    skipped.append(f"{b['label']}: already set")
                    continue
                token = new_token()
                text, needles = bait_text(kind, token, callback)
                try:
                    if kind == "git_branch":
                        rec = self._plant_branch(Path(where), text, allow_public_repo)
                    else:
                        rec = self._plant_file(self._resolve(where), text, allow_public_repo)
                except TripwireError as exc:
                    skipped.append(f"{b['label']}: {exc}")
                    continue
                st["planted"][token] = {"kind": kind, "needles": needles, "planted_at": self.clock(), "hits": 0,
                                        "target": where, **rec}
                done.append(f"{b['label']} ({rec['where']})")
            self._save(st)
        return {"planted": done, "skipped": skipped,
                "message": (f"Set {len(done)} tripwire{'s' if len(done) != 1 else ''}." if done else
                            "No new tripwires set.") + (" Skipped: " + "; ".join(skipped) if skipped else "")}

    def _plant_file(self, p: Path, text: str, allow_public_repo: bool) -> dict[str, Any]:
        if self._inside_public_repo(p) and not allow_public_repo:
            raise TripwireError("that's inside Orion's public repo, so it was left out")
        if p.exists():
            raise TripwireError("a real file is already there, so it was left alone")
        p.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(p), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        return {"file": str(p), "where": str(p).replace(str(self.home), "~", 1)}

    def _git(self, repo: Path, *args: str, stdin: Optional[str] = None) -> str:
        env = {**os.environ, "GIT_AUTHOR_NAME": "deploy", "GIT_AUTHOR_EMAIL": "deploy@localhost",
               "GIT_COMMITTER_NAME": "deploy", "GIT_COMMITTER_EMAIL": "deploy@localhost",
               "GIT_AUTHOR_DATE": "2023-03-14T18:22:05", "GIT_COMMITTER_DATE": "2023-03-14T18:22:05"}
        r = self.run(["git", "-C", str(repo), *args], input=stdin, capture_output=True, text=True, timeout=30,
                     env=env)
        if r.returncode != 0:
            raise TripwireError(f"git said: {(r.stderr or '').strip()[:120] or 'no'}")
        return (r.stdout or "").strip()

    def _plant_branch(self, repo: Path, text: str, allow_public_repo: bool) -> dict[str, Any]:
        """An old-looking branch with one deploy file, made with git plumbing: no checkout, your files untouched."""
        repo = repo.expanduser()
        if not (repo / ".git").exists():
            raise TripwireError("that folder isn't a git repo")
        if self._inside_public_repo(repo) and not allow_public_repo:
            raise TripwireError("that's Orion's public repo, so no branch was added")
        try:
            self._git(repo, "rev-parse", "--verify", "--quiet", f"refs/heads/{GIT_BRANCH}")
            raise TripwireError(f"a branch called {GIT_BRANCH} already exists")
        except TripwireError as exc:
            if "already exists" in str(exc):
                raise
        blob = self._git(repo, "hash-object", "-w", "--stdin", stdin=text)
        tree = self._git(repo, "mktree", stdin=f"100644 blob {blob}\t.env.production\n")
        commit = self._git(repo, "commit-tree", tree, "-m", "old deploy settings")
        self._git(repo, "update-ref", f"refs/heads/{GIT_BRANCH}", commit, "0" * 40)
        return {"repo": str(repo), "branch": GIT_BRANCH, "commit": commit,
                "where": f"{str(repo).replace(str(self.home), '~', 1)}#{GIT_BRANCH}"}

    def remove(self) -> dict[str, Any]:
        """Take away every bait Orion planted (files only while they still hold their token)."""
        removed, kept = 0, []
        with self._lock:
            st = self._load()
            for token, p in list(st["planted"].items()):
                try:
                    if p.get("file"):
                        f = Path(p["file"])
                        if f.exists():
                            if token not in f.read_text(encoding="utf-8", errors="replace"):
                                kept.append(f"{p.get('where')} (changed since, left alone)")
                                del st["planted"][token]
                                continue
                            f.unlink()
                    elif p.get("repo"):
                        self._git(Path(p["repo"]), "update-ref", "-d", f"refs/heads/{p['branch']}", p["commit"])
                except (OSError, TripwireError) as exc:
                    kept.append(f"{p.get('where')} ({exc})")
                del st["planted"][token]
                removed += 1
            self._save(st)
        return {"removed": removed, "kept": kept,
                "message": f"Removed {removed} tripwire{'s' if removed != 1 else ''}."
                           + (" Left alone: " + "; ".join(kept) if kept else "")}

    # ------------------------------------------------------------------ the alarm
    def match(self, haystack: str) -> Optional[str]:
        """The token of the bait whose needle appears in the request, if any."""
        if MARKER not in haystack.lower():
            return None
        low = haystack.lower()
        for token, p in self._load()["planted"].items():
            for n in p.get("needles") or [token]:
                if n and n.lower() in low:
                    return token
        return None

    def record_hit(self, *, path: str, ip: str, user_agent: str = "", method: str = "GET",
                   headers: Optional[dict[str, str]] = None, body: bytes = b"", via: str = "daemon") -> bool:
        """A request reached the alarm. True when it used one of Orion's bait (and the alarm went off)."""
        hay = " ".join([path, *(f"{k}: {v}" for k, v in (headers or {}).items()),
                        body[:4096].decode("utf-8", errors="replace")])
        token = self.match(hay)
        if token is None:
            return False
        now = self.clock()
        with self._lock:
            st = self._load()
            p = st["planted"].get(token)
            if p is None:
                return False
            label = KINDS.get(p["kind"], {}).get("label", p["kind"])
            quiet = p.get("last_alert") and now - float(p["last_alert"]) < HIT_QUIET_S
            p["hits"] = int(p.get("hits", 0)) + 1
            p["last_hit"] = now
            if not quiet:
                p["last_alert"] = now
            hit = {"at": now, "kind": p["kind"], "label": label, "where": p.get("where", ""),
                   "ip": str(ip or "?")[:64], "user_agent": str(user_agent or "")[:200],
                   "method": method[:10], "path": re.sub(TOKEN_RE, "<token>", path)[:200], "via": via}
            st["hits"] = (st["hits"] + [hit])[-MAX_HITS:]
            self._save(st)
        if not quiet:
            body_txt = (f"Someone used {label} ({hit['where']}) from {hit['ip']}"
                        + (f" with {hit['user_agent'][:80]}" if hit["user_agent"] else "")
                        + ". Nothing real was exposed: it was bait. Check who is on that machine.")
            try:
                self.notify("system", "Tripwire: someone is inside", body_txt, ref=f"tripwire:{token[-6:]}",
                            level="urgent")
            except Exception:
                log.exception("tripwire alert failed")
            if self.publish:
                try:
                    self.publish({"event": "TRIPWIRE_HIT", "hit": hit})
                except Exception:
                    pass
        return True

    def ingest_inbox(self) -> int:
        """Hits written by a listener that ran without the daemon."""
        try:
            data = self.hits_inbox.read_bytes()
        except OSError:
            return 0
        with self._lock:
            st = self._load()
            off = int(st.get("inbox_offset") or 0)
            if off > len(data):
                off = 0
            new = data[off:]
            st["inbox_offset"] = len(data)
            self._save(st)
        n = 0
        for line in new.decode("utf-8", errors="replace").splitlines():
            try:
                h = json.loads(line)
                if self.record_hit(path=h.get("path", ""), ip=h.get("ip", ""), user_agent=h.get("ua", ""),
                                   method=h.get("method", "GET"), headers=h.get("headers") or {},
                                   body=str(h.get("body") or "").encode(), via="listener"):
                    n += 1
            except (ValueError, AttributeError):
                continue
        return n

    # ------------------------------------------------------------------ the small listener (server profile)
    def start_listener(self, bind: Optional[str] = None, port: Optional[int] = None,
                       record: Optional[Callable[..., Any]] = None) -> int:
        if self._server is not None:
            return self._server.server_address[1]
        lis = self.config()["listener"]
        rec = record or self.record_hit

        class Handler(BaseHTTPRequestHandler):
            server_version = "nginx"
            sys_version = ""

            def _any(self) -> None:
                n = min(int(self.headers.get("Content-Length") or 0), 65536)
                body = self.rfile.read(n) if n > 0 else b""
                try:
                    rec(path=self.path, ip=self.client_address[0], user_agent=self.headers.get("User-Agent", ""),
                        method=self.command, headers={k: v for k, v in self.headers.items()}, body=body,
                        via="listener")
                except Exception:
                    log.exception("tripwire listener failed")
                out = b'{"error":"invalid credentials"}'
                self.send_response(403)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(out)))
                self.end_headers()
                if self.command != "HEAD":
                    self.wfile.write(out)

            do_GET = do_POST = do_PUT = do_DELETE = do_PATCH = do_HEAD = _any

            def log_message(self, *a: Any) -> None:
                return

        self._server = ThreadingHTTPServer((bind or lis["bind"], int(port if port is not None else lis["port"])),
                                           Handler)
        self._server.daemon_threads = True
        threading.Thread(target=self._server.serve_forever, name="tripwire-listener", daemon=True).start()
        return self._server.server_address[1]

    def stop_listener(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None

    def inbox_writer(self) -> Callable[..., bool]:
        """For a listener running without the daemon: keep hits in a file for the daemon."""
        def rec(**h: Any) -> bool:
            if self.match(" ".join([h.get("path", ""), json.dumps(h.get("headers") or {}),
                                    (h.get("body") or b"")[:4096].decode("utf-8", errors="replace")])) is None:
                return False
            line = {"path": h.get("path"), "ip": h.get("ip"), "ua": h.get("user_agent"), "method": h.get("method"),
                    "headers": h.get("headers"), "body": (h.get("body") or b"")[:4096].decode("utf-8", "replace"),
                    "at": self.clock()}
            self.dir.mkdir(parents=True, exist_ok=True)
            with self.hits_inbox.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(line) + "\n")
            return True
        return rec


def _alert(kind: str, title: str, body: str = "", ref: Optional[str] = None, level: Optional[str] = None) -> None:
    import alerts
    try:
        alerts.emit(kind, title, body, ref=ref, level=level)
    except TypeError:          # an older alerts module without levels
        alerts.emit(kind, title, body, ref=ref)
    try:
        import linux_desktop
        linux_desktop.notify(title, body)
    except Exception:
        pass


def _ago(ts: Any) -> str:
    try:
        s = time.time() - float(ts)
    except (TypeError, ValueError):
        return "just now"
    return "just now" if s < 120 else f"{int(s // 60)} min ago" if s < 7200 else f"{int(s // 3600)} h ago" \
        if s < 172800 else f"{int(s // 86400)} days ago"


# =====================================================================================  approvals
def register_task_type() -> None:
    import boardroom
    from boardroom import TaskType
    boardroom.TASK_TYPES.setdefault(TASK, TaskType(TASK, "Set tripwires", "infra", reversible=True, money=False,
                                                   sensitive=True))


async def propose(board: Any, tw: Tripwires, target: str = "this computer", asked_by: str = "you") -> dict[str, Any]:
    plan = tw.plan()
    if not plan:
        return {"ok": False, "message": "No bait is set up. Pick some in Settings > Tripwires."}
    register_task_type()
    board.store.x("INSERT OR IGNORE INTO autonomy(task_type) VALUES(?)", (TASK,))
    where = "; ".join(f"{p['label']} at {p['where']}" for p in plan)
    dec = await board.propose(TASK, f"Set tripwires on {target}: {len(plan)} pieces of bait",
                              {"target": target, "plan": plan, "callback": tw.config()["callback"],
                               "asked_by": asked_by[:60], "where": where[:900]},
                              proposer="Tripwires", risk="low", simulated=False)
    return {"ok": True, "decision": dec["id"], "status": dec.get("status"),
            "message": f"Asked your phone to set {len(plan)} tripwire{'s' if len(plan) != 1 else ''} on {target}."}


def make_executor(tw: Tripwires, after: Optional[Callable[[], None]] = None) -> Callable[[dict[str, Any]], Any]:
    def execute(d: dict[str, Any]) -> dict[str, Any]:
        res = tw.plant()
        target = str(((d.get("packet") or {}).get("details") or {}).get("target") or "")
        if after and ("server" in target):
            try:
                after()
            except Exception:
                log.exception("tripwire listener start failed")
        return res
    return execute


# =====================================================================================  HTTP
def make_router(get_tw: Callable[[], Tripwires], get_board: Callable[[], Any]):
    from fastapi import APIRouter, Request
    from fastapi.responses import JSONResponse

    router = APIRouter(tags=["toolkit"])

    async def body_of(request: Request) -> dict[str, Any]:
        try:
            b = await request.json()
        except ValueError:
            return {}
        return b if isinstance(b, dict) else {}

    @router.get("/tripwires")
    async def state() -> Any:
        return get_tw().public()

    @router.post("/tripwires/plant")
    async def plant(request: Request) -> Any:
        b = await body_of(request)
        return await propose(get_board(), get_tw(), str(b.get("target") or "this computer")[:60], "Settings")

    @router.post("/tripwires/remove")
    async def remove() -> Any:
        import asyncio
        return await asyncio.to_thread(get_tw().remove)

    @router.post("/tripwires/config")
    async def config(request: Request) -> Any:
        try:
            return {"config": get_tw().set_config(await body_of(request))}
        except (TripwireError, ValueError, TypeError) as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)

    @router.api_route("/tripwire/{rest:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "HEAD"])
    async def alarm(rest: str, request: Request) -> Any:
        body = await request.body()
        get_tw().record_hit(path="/tripwire/" + rest + (("?" + request.url.query) if request.url.query else ""),
                            ip=request.client.host if request.client else "?",
                            user_agent=request.headers.get("user-agent", ""), method=request.method,
                            headers=dict(request.headers), body=body[:65536])
        return JSONResponse({"error": "invalid credentials"}, status_code=403)

    return router


# =====================================================================================  command line
def main(argv: Optional[list[str]] = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="tripwires.py", description="Orion tripwires: the small listener.")
    sub = ap.add_subparsers(dest="cmd")
    li = sub.add_parser("listen", help="listen for bait being used (when the daemon isn't running here)")
    li.add_argument("--bind", default="0.0.0.0")
    li.add_argument("--port", type=int, default=DEFAULT_LISTENER["port"])
    li.add_argument("--data-dir", default=os.environ.get("ORION_DATA_DIR") or str(Path.home() / ".orion"))
    a = ap.parse_args(argv)
    if a.cmd != "listen":
        ap.print_help()
        return 2
    tw = Tripwires(Path(a.data_dir))
    port = tw.start_listener(a.bind, a.port, record=tw.inbox_writer())
    print(f"Listening for tripwires on {a.bind}:{port}. Hits go to {tw.hits_inbox} for Orion to pick up.")
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        tw.stop_listener()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
