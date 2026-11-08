"""Kill switch: "Orion, freeze" (docs/ORIONOS_COMPLETE_PLAN.md Part 3 item 7).

One phone button, the HUD button, `orion-ctl freeze` or saying "Orion, freeze" stops all self-changes, updates and
agents until you unfreeze. While frozen:
  - Orion changes nothing about itself: self-evolve patches, crash fixes, decisions that would run on their own
  - automatic updates stop: unattended-upgrades is paused (a root drop-in, through a fixed helper), and the weekly
    Get apps run, orion-update and orion-slot update refuse to start (they check the frozen marker)
  - coding-agent tools that change things (MCP run / commit / database writes) and skill replays are refused
  - coding agents Orion started are paused (not killed; they carry on after you unfreeze)
Snapshots still work, and you can still read everything.

Freezing is one tap. Unfreezing needs your phone (fingerprint) or a signed boardroom decision: a voice command, a
chat message or the HUD can only *ask* to unfreeze.

Other modules call `is_frozen()` (cheap: one small file, cached by its modification time) or `blocked(what)`.

The state lives in data_dir/freeze.json, with a marker file data_dir/frozen for shell tools. A program running as
your own user could delete those files: the kill switch stops Orion, it isn't a lock against other software.

HTTP (local):  GET /freeze   POST /freeze {why?}   POST /freeze/unfreeze (asks for approval)
Phone (signed with the fingerprint key):  POST /phone/freeze   POST /phone/unfreeze
"""

import base64
import json
import logging
import os
import re
import secrets
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any, Callable, Optional

log = logging.getLogger("orion.freeze")

MESSAGE = "Orion is frozen, so it won't change anything right now. Unfreeze it from your phone first."
UNFREEZE_TASK = "safety.unfreeze"
HELPER = "/opt/orion/safety/orion-freeze-helper"

FREEZE_PHRASES = re.compile(r"^(freeze|freeze( everything| now| orion| all)?|orion freeze|stop everything now|"
                            r"emergency stop)$")
UNFREEZE_PHRASES = re.compile(r"^(unfreeze|unfreeze( everything| orion| now)?|thaw( out)?)$")

_dir_override: Optional[Path] = None
_cache: tuple[float, dict[str, Any]] = (-1.0, {})


def data_dir() -> Path:
    if _dir_override is not None:
        return _dir_override
    from config import settings
    return Path(settings.data_dir)


def set_dir(path: Optional[Path]) -> None:
    """Tests: keep the state somewhere else."""
    global _dir_override, _cache
    _dir_override = Path(path) if path is not None else None
    _cache = (-1.0, {})


def _state_path() -> Path:
    return data_dir() / "freeze.json"


def _marker() -> Path:
    return data_dir() / "frozen"


def state() -> dict[str, Any]:
    global _cache
    p = _state_path()
    try:
        mtime = p.stat().st_mtime
    except OSError:
        return {"frozen": False}
    if mtime == _cache[0]:
        return dict(_cache[1])
    try:
        st = json.loads(p.read_text(encoding="utf-8"))
        st = st if isinstance(st, dict) else {}
    except (OSError, ValueError):
        st = {"frozen": True, "why": "the freeze file couldn't be read, so Orion stays frozen to be safe"}
    st["frozen"] = bool(st.get("frozen"))
    _cache = (mtime, st)
    return dict(st)


def is_frozen() -> bool:
    try:
        return bool(state().get("frozen"))
    except Exception:
        return False


def blocked(what: str = "") -> Optional[str]:
    """None when allowed; otherwise the plain sentence to show."""
    if not is_frozen():
        return None
    return f"Orion is frozen, so it won't {what} right now. Unfreeze it from your phone first." if what else MESSAGE


def _write(st: dict[str, Any]) -> None:
    d = data_dir()
    d.mkdir(parents=True, exist_ok=True)
    tmp = _state_path().with_suffix(".tmp")
    tmp.write_text(json.dumps(st, indent=1), encoding="utf-8")
    os.replace(tmp, _state_path())
    if st.get("frozen"):
        _marker().write_text(f"frozen since {time.strftime('%Y-%m-%d %H:%M')}\n", encoding="utf-8")
    else:
        try:
            _marker().unlink()
        except FileNotFoundError:
            pass


def _system_pause(on: bool, run: Callable[..., Any] = subprocess.run, approval: Optional[str] = None) -> str:
    """Pause or resume automatic system updates (unattended-upgrades) through the fixed root helper.
    Best effort: on a computer without the helper (Windows, a dev checkout) it says so and carries on.
    Resuming needs your password (polkit auth_self) unless `approval` is the phone's signed request (H9): the
    helper checks it against the phone key root pinned at pairing (os/safety/orion-approval-check)."""
    if not os.path.exists(HELPER) or not shutil.which("pkexec"):
        return "Automatic system updates: no helper on this computer, nothing to pause."
    argv = ["pkexec", HELPER, "on" if on else "off"] + (["--approval", approval] if approval and not on else [])
    try:
        r = run(argv, capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return f"Automatic system updates: couldn't reach the helper ({exc})."
    if getattr(r, "returncode", 1) != 0:
        return "Automatic system updates: the helper refused (" + ((r.stderr or "").strip()[-120:] or "no reason") + ")."
    return "Automatic system updates paused." if on else "Automatic system updates back on."


def _launched_agents(daemon: Any) -> Any:
    tk = getattr(daemon, "agent_toolkit", None)
    return getattr(tk, "swarm", None)


async def _pause_agents(daemon: Any, action: str) -> list[str]:
    """Pause (or resume) the coding agents Orion itself started. Agents you started yourself aren't touched."""
    swarm = _launched_agents(daemon)
    if swarm is None:
        return []
    done = []
    try:
        for a in swarm.launched():
            if not a.get("running"):
                continue
            if action == "pause" and a.get("paused"):
                continue
            if action == "resume" and a["id"] not in (state().get("paused_agents") or []):
                continue
            run = (getattr(swarm.runner, "runs", None) or {}).get(a["id"])
            if run is None or getattr(run, "proc", None) is None:
                continue
            try:
                swarm._signal(run.proc.pid, action)
                (swarm.paused.add if action == "pause" else swarm.paused.discard)(a["id"])
                done.append(a["id"])
            except Exception as exc:
                log.warning("couldn't %s agent %s: %s", action, a["id"], exc)
    except Exception:
        log.exception("agent %s on freeze failed", action)
    return done


async def _announce(daemon: Any, st: dict[str, Any]) -> None:
    try:
        await daemon.bus.publish({"event": "FREEZE_STATE", "freeze": public(st)})
    except Exception:
        pass


def public(st: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    st = state() if st is None else st
    return {"frozen": bool(st.get("frozen")), "since": st.get("since"), "by": st.get("by", ""),
            "why": st.get("why", ""), "system_updates": st.get("system_updates", ""),
            "paused_agents": len(st.get("paused_agents") or []), "unfreeze_request": st.get("unfreeze_request")}


async def freeze(daemon: Any, by: str, why: str = "", run: Callable[..., Any] = subprocess.run) -> dict[str, Any]:
    """Freeze now. Safe to call twice."""
    cur = state()
    if cur.get("frozen"):
        return public(cur)
    st = {"frozen": True, "since": time.time(), "by": " ".join(str(by).split())[:60] or "you",
          "why": " ".join(str(why or "").split())[:200]}
    _write(st)        # first, so every guard sees it before anything else happens
    import asyncio
    st["system_updates"] = await asyncio.to_thread(_system_pause, True, run)
    st["paused_agents"] = await _pause_agents(daemon, "pause")
    _write(st)
    try:
        import changes
        changes.record_change("Froze Orion: no self-changes, updates or agents until you unfreeze",
                              why=st["why"] or "you pressed the kill switch", kind="freeze", source="kill switch",
                              approved_by=st["by"], result=st["system_updates"],
                              undo={"kind": "unfreeze", "label": "Unfreeze (needs your phone)", "sensitive": True})
    except Exception:
        pass
    try:
        import alerts
        alerts.emit("system", "Orion is frozen", "No self-changes, updates or agents until you unfreeze it from "
                    "this phone.", ref="freeze")
    except Exception:
        pass
    await _announce(daemon, st)
    try:
        await daemon.bus.log(f"Frozen by {st['by']}. {st['system_updates']}", "warn")
    except Exception:
        pass
    return public(st)


async def unfreeze(daemon: Any, by: str, run: Callable[..., Any] = subprocess.run,
                   approval: Optional[str] = None) -> dict[str, Any]:
    """Only for approved paths: the phone's fingerprint key, or a signed boardroom decision."""
    cur = state()
    if not cur.get("frozen"):
        return public(cur)
    import asyncio
    sys_msg = await asyncio.to_thread(_system_pause, False, run, approval)
    if approval:
        try:
            os.unlink(approval)
        except OSError:
            pass
    resumed = await _pause_agents(daemon, "resume")
    st = {"frozen": False, "unfrozen_at": time.time(), "by": " ".join(str(by).split())[:60]}
    _write(st)
    try:
        import changes
        changes.record_change("Unfroze Orion", why="you unfroze it", kind="unfreeze", source="kill switch",
                              approved_by=st["by"], result=f"{sys_msg} {len(resumed)} agent(s) carried on.".strip())
    except Exception:
        pass
    try:
        import alerts
        alerts.emit("system", "Orion is running again", "Updates, self-changes and agents can go ahead again.",
                    ref="freeze")
    except Exception:
        pass
    await _announce(daemon, st)
    return public(st)


def _register_task_type() -> None:
    import boardroom
    from boardroom import TaskType
    boardroom.TASK_TYPES.setdefault(UNFREEZE_TASK, TaskType(UNFREEZE_TASK, "Unfreeze Orion", "system",
                                                            reversible=True, money=False, sensitive=True))


async def request_unfreeze(daemon: Any, asked_by: str = "you") -> dict[str, Any]:
    """From the HUD, voice or chat: only asks. The answer comes from the phone's fingerprint or the boardroom."""
    st = state()
    if not st.get("frozen"):
        return {"ok": True, "message": "Orion isn't frozen."}
    board = getattr(daemon, "board", None)
    if board is None:
        return {"ok": False, "message": "Orion is still starting."}
    pending = st.get("unfreeze_request")
    if pending:
        d = board.get(pending)
        if d is not None and d.get("status") == "pending":
            return {"ok": True, "pending": True, "decision": pending,
                    "message": "Already waiting for your OK on your phone to unfreeze."}
    _register_task_type()
    board.store.x("INSERT OR IGNORE INTO autonomy(task_type) VALUES(?)", (UNFREEZE_TASK,))
    dec = await board.propose(UNFREEZE_TASK, "Unfreeze Orion: let updates, self-changes and agents run again",
                              {"frozen_since": st.get("since"), "frozen_by": st.get("by", ""),
                               "asked_by": asked_by[:60]},
                              proposer="Kill switch", risk="medium", simulated=False)
    st["unfreeze_request"] = dec["id"]
    _write(st)
    return {"ok": True, "pending": True, "decision": dec["id"],
            "message": "Asked your phone. Orion stays frozen until you approve it there."}


def install(daemon: Any) -> None:
    """Register the unfreeze decision's executor and the phone routes. Call before the boardroom starts."""
    _register_task_type()
    board = getattr(daemon, "board", None)
    if board is None:
        return

    def execute(d: dict[str, Any]) -> dict[str, Any]:
        import asyncio
        asyncio.get_running_loop().create_task(unfreeze(daemon, "your approval"))
        return {"started": True}

    board.executors[UNFREEZE_TASK] = execute
    try:
        board.store.x("INSERT OR IGNORE INTO autonomy(task_type) VALUES(?)", (UNFREEZE_TASK,))
    except Exception:
        pass
    board.remote_hooks.append(phone_hook(daemon))

    try:   # the change history's "Unfreeze" undo goes through the same approval
        import changes

        async def undo_unfreeze(dmn: Any, change: dict[str, Any], undo: dict[str, Any]) -> dict[str, Any]:
            await unfreeze(dmn, "your approval")
            return {"ok": True, "message": "Unfrozen."}
        changes.register_undo("unfreeze", undo_unfreeze)
    except Exception:
        pass


async def save_approval_token(request: Any, who: dict[str, Any]) -> Optional[str]:
    """The phone's signed request, exactly as it arrived, in a private file the root helper can verify (it knows
    the phone's key from pairing). Returns the path, or None (then resuming system updates asks for a password)."""
    try:
        body = await request.body()
        tok = {"device": who.get("device_id") or request.headers.get("x-orion-device", ""), "method": request.method,
               "path": request.url.path, "ts": request.headers.get("x-orion-ts", ""),
               "sig": request.headers.get("x-orion-sig", ""), "body_b64": base64.b64encode(body).decode()}
        d = Path(os.environ.get("XDG_RUNTIME_DIR") or data_dir()) / "orion-approvals"
        d.mkdir(mode=0o700, parents=True, exist_ok=True)
        p = d / f"{secrets.token_hex(8)}.json"
        fd = os.open(str(p), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(tok, f)
        return str(p)
    except Exception:
        log.debug("approval token not saved", exc_info=True)
        return None


def phone_hook(daemon: Any) -> Callable[..., None]:
    def hook(app: Any, phone_auth: Any, json_body: Any) -> None:
        from fastapi import Request

        @app.post("/phone/freeze")
        async def phone_freeze(request: Request):
            who, err = await phone_auth(request, "sign")
            if err is not None:
                return err
            data = await json_body(request) or {}
            return {"freeze": await freeze(daemon, f"phone: {who['name']}", str(data.get("why") or "")[:200])}

        @app.post("/phone/unfreeze")
        async def phone_unfreeze(request: Request):
            who, err = await phone_auth(request, "sign")   # the fingerprint key: this *is* the approval
            if err is not None:
                return err
            token = await save_approval_token(request, who)   # H9: root checks the phone's signature itself
            return {"freeze": await unfreeze(daemon, f"phone: {who['name']} {who['fingerprint']}", approval=token)}

        @app.get("/phone/freeze")
        async def phone_freeze_state(request: Request):
            who, err = await phone_auth(request, "read")
            if err is not None:
                return err
            return {"freeze": public()}

    return hook


async def handle_text(daemon: Any, text: str, source: str = "user") -> Optional[str]:
    """"Orion, freeze" from the HUD, voice, phone chat or a quick command. Returns the reply, or None."""
    try:
        from quick_commands import normalize
    except ImportError:
        return None
    t = normalize(text)
    if FREEZE_PHRASES.match(t):
        st = await freeze(daemon, {"voice": "your voice", "phone": "phone chat"}.get(source, "you"))
        return ("Frozen. No self-changes, updates or agents until you unfreeze it from your phone."
                + (f" Paused {st['paused_agents']} agent(s)." if st.get("paused_agents") else ""))
    if UNFREEZE_PHRASES.match(t):
        return (await request_unfreeze(daemon, source))["message"]
    return None


def make_router(get_daemon: Callable[[], Any]):
    from fastapi import APIRouter, Request

    router = APIRouter(prefix="/freeze", tags=["safety"])
    try:
        install(get_daemon())
    except Exception:
        log.debug("freeze not installed yet", exc_info=True)

    @router.get("")
    async def freeze_state() -> Any:
        return {"freeze": public()}

    @router.post("")
    async def freeze_now(request: Request) -> Any:
        try:
            b = await request.json()
        except ValueError:
            b = {}
        b = b if isinstance(b, dict) else {}
        return {"freeze": await freeze(get_daemon(), str(b.get("by") or "you")[:40], str(b.get("why") or ""))}

    @router.post("/unfreeze")
    async def unfreeze_ask() -> Any:
        return await request_unfreeze(get_daemon(), "you")

    return router
