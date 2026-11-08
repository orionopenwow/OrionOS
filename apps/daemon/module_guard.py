"""One broken optional module must not take Orion's whole brain down (H15).

main.py loads ~100 modules into one process. The core ones (bus, memory, orchestrator, boardroom...) are imported
normally: without them nothing works anyway. The optional ones (Toolkit tools, Get apps, Stardust, outreach,
safety net extras...) go through here: if importing, wiring or starting one fails, that one is switched off, the
rest carries on, and GET /health/full says which one and why in plain words.

    mod = module_guard.load("secret_shield")                        # the module, or None
    module_guard.router(app, "secret_shield", lambda: mod.make_router(get))   # include_router, guarded
    module_guard.start("backup", lambda: backup.start(daemon))      # sync start, guarded
    await module_guard.astart("agent_safety", daemon.agent_safety.start)    # async start, guarded
"""
from __future__ import annotations

import importlib
import inspect
import logging
import time
import traceback
from typing import Any, Callable, Optional

log = logging.getLogger("orion.modules")

_failed: dict[str, dict[str, Any]] = {}
_ok: set[str] = set()


def _fail(name: str, stage: str, exc: BaseException) -> None:
    tb = traceback.extract_tb(exc.__traceback__)
    where = f"{tb[-1].filename.replace(chr(92), '/').rsplit('/', 1)[-1]}:{tb[-1].lineno}" if tb else ""
    _failed[name] = {"module": name, "stage": stage, "error": f"{type(exc).__name__}: {str(exc)[:300]}",
                     "where": where, "at": time.time()}
    _ok.discard(name)
    log.error("Optional module %s failed to %s and is switched off: %s", name, stage, exc, exc_info=exc)


def load(name: str) -> Optional[Any]:
    try:
        mod = importlib.import_module(name)
    except Exception as exc:   # SyntaxError, ImportError, a crash at import time...
        _fail(name, "load", exc)
        return None
    _ok.add(name)
    return mod


def router(app: Any, name: str, make: Callable[[], Any]) -> bool:
    if name in _failed:
        return False
    try:
        r = make()
        if r is not None:
            app.include_router(r)
    except Exception as exc:
        _fail(name, "add its routes", exc)
        return False
    _ok.add(name)
    return True


def call(name: str, fn: Callable[[], Any], stage: str = "start") -> Any:
    if name in _failed:
        return None
    try:
        return fn()
    except Exception as exc:
        _fail(name, stage, exc)
        return None


def start(name: str, fn: Callable[[], Any]) -> Any:
    return call(name, fn, "start")


async def astart(name: str, fn: Callable[[], Any]) -> Any:
    if name in _failed:
        return None
    try:
        res = fn()
        if inspect.isawaitable(res):
            res = await res
        return res
    except Exception as exc:
        _fail(name, "start", exc)
        return None


async def astop(name: str, fn: Callable[[], Any]) -> None:
    if name in _failed:
        return
    try:
        res = fn()
        if inspect.isawaitable(res):
            await res
    except Exception:
        log.exception("Optional module %s failed to stop", name)


def failed() -> list[dict[str, Any]]:
    return sorted(_failed.values(), key=lambda f: f["at"])


def report() -> dict[str, Any]:
    f = failed()
    return {"ok": not f, "modules_ok": sorted(_ok - set(_failed)), "modules_off": f,
            "text": ("All of Orion's parts are running." if not f else
                     f"{len(f)} part(s) of Orion are switched off because they failed to start: "
                     + ", ".join(x["module"] for x in f) + ". Everything else works.")}


def make_router(get_daemon: Callable[[], Any]):
    from fastapi import APIRouter

    r = APIRouter(tags=["health"])

    @r.get("/health/full")
    async def health_full() -> Any:
        d = get_daemon()
        out = {"status": "ok" if not _failed else "degraded", "modules": report()}
        try:
            out["agents"] = dict(d.agents)
        except Exception:
            pass
        try:
            cap = getattr(d, "capture", None)
            out["screen_reading"] = {"on": bool(cap and cap.is_alive() and not cap.paused),
                                     "skipped_because": getattr(cap, "last_skip_reason", "") if cap else ""}
        except Exception:
            pass
        return out

    return r


def _reset_for_tests() -> None:
    _failed.clear()
    _ok.clear()
