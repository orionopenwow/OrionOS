"""Ask the Orion window to open one of its panels: POST /hud/open {"what": "phone"} (needs the daemon's key).

"Start Here.cmd" uses it to open the Phone panel at the pairing step. It only broadcasts the HUD_OPEN event the
window already listens for (TestCenter.tsx forwards any panel name to the menu). It opens a window, nothing more:
it never presses a button in it.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Callable

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

log = logging.getLogger("orion.hud_open")

HUD_OPEN_EVENT = "HUD_OPEN"
_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")


def hud_connected(daemon: Any) -> bool:
    bus = getattr(daemon, "bus", None)
    clients = getattr(bus, "clients", None)
    try:
        return any(getattr(c, "kind", "") != "extension" for c in (clients or {}).values())
    except Exception:
        return False


async def open_panel(daemon: Any, what: str) -> dict[str, Any]:
    if not _NAME.match(what or ""):
        raise ValueError("Say which panel, in small letters (for example phone).")
    bus = getattr(daemon, "bus", None)
    if bus is None:
        raise RuntimeError("Orion is still starting.")
    await bus.publish({"event": HUD_OPEN_EVENT, "what": what}, record=False)
    return {"ok": True, "what": what, "window": hud_connected(daemon)}


def make_router(get_daemon: Callable[[], Any]) -> APIRouter:
    r = APIRouter()

    @r.post("/hud/open")
    async def hud_open(request: Request):
        try:
            body = await request.json()
        except Exception:
            body = {}
        what = str(body.get("what", "")) if isinstance(body, dict) else ""
        try:
            return await open_panel(get_daemon(), what)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        except RuntimeError as exc:
            return JSONResponse({"error": str(exc)}, status_code=503)

    return r
