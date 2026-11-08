"""Everyone can use it, daemon side (docs/ORIONOS_COMPLETE_PLAN.md Part 1, step 6; os/everyone/README.md).

Two small things:

1. **Accessibility for the Orion window.** Settings > Accessibility (orion-access) keeps its choices in
   ~/.config/orion/access.json. GET /access hands the HUD the parts it shows itself: high contrast, text size and
   its own zoom.

2. **Guest mode.** In a guest session (the account named by ORION_GUEST_USER, "guest", or ORION_GUEST_MODE=1 from
   the guest's daemon.env) Orion is a plain assistant: you can talk to it and it can talk back, but no agents run,
   no messages are read or sent, nothing waits for approval, and no phone, boardroom, money or install requests
   exist. Anything else is refused with 403 over HTTP and a short note over the WebSocket. The guest's home is a
   RAM disk wiped at log-out, so whatever Orion remembers there is gone too.

    GET /access   {contrast, text_scale, hud_zoom, screen_reader, keyboard}
    GET /guest    {guest: bool}
"""
from __future__ import annotations

import json
import logging
import os
try:
    import pwd  # Linux only; Windows has no guest account to detect
except ImportError:
    pwd = None
from pathlib import Path
from typing import Any, Callable

log = logging.getLogger("orion.everyone")

GUEST_USER = os.environ.get("ORION_GUEST_USER", "guest")
# What a guest may use. Everything else (approvals, comms, agents, boardroom, phone, setup, apps, stardust,
# outreach, automation, vault, self-evolve, MCP, toolkit, ...) is refused. Allow-list, so new features start closed.
GUEST_HTTP = {
    "GET": ("/health", "/guest", "/access", "/tts/voices", "/ollama/status", "/linux/status", "/pacing", "/memory/",
            "/quick", "/hotmic", "/events/"),
    "POST": ("/tts/speak", "/say"),
}
GUEST_EVENTS = frozenset({
    "CLIENT_HELLO", "USER_PROMPT", "VOICE_TRANSCRIPT", "SPEAK", "AUDIO_CUE", "MEMORY_QUERY", "PING", "SET_TTS_VOICE",
    "SET_PERSONALITY", "PACING_ACK",
})
TEXT_SCALES = (1.0, 1.25, 1.5, 1.75, 2.0)


def guest_mode() -> bool:
    if os.environ.get("ORION_GUEST_MODE", "").strip().lower() in ("1", "true", "yes", "on"):
        return True
    if pwd is None:
        return False
    try:
        return pwd.getpwuid(os.getuid()).pw_name == GUEST_USER
    except (KeyError, AttributeError):
        return False


def http_allowed(method: str, path: str) -> bool:
    """May a guest session use this route? (Only asked in guest mode.)"""
    if method == "OPTIONS":
        return True
    path = path.rstrip("/") or "/"
    for allowed in GUEST_HTTP.get(method.upper(), ()):
        if allowed.endswith("/") and path.startswith(allowed):
            return True
        if path == allowed:
            return True
    return False


def ws_allowed(event: Any) -> bool:
    return event in GUEST_EVENTS


def access_file() -> Path:
    return Path(os.environ.get("ORION_ACCESS_FILE", os.path.expanduser("~/.config/orion/access.json")))


def read_access(path: Path | None = None) -> dict[str, Any]:
    try:
        raw = json.loads((path or access_file()).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raw = {}
    raw = raw if isinstance(raw, dict) else {}

    def num(key: str, lo: float, hi: float) -> float:
        try:
            return round(min(hi, max(lo, float(raw.get(key, 1.0)))), 2)
        except (TypeError, ValueError):
            return 1.0
    ts = num("text_scale", 1.0, 2.0)
    return {"contrast": raw.get("contrast") is True, "text_scale": min(TEXT_SCALES, key=lambda c: abs(c - ts)),
            "hud_zoom": num("hud_zoom", 0.5, 3.0), "screen_reader": raw.get("screen_reader") is True,
            "keyboard": raw.get("keyboard") is True}


def limit_daemon(daemon: Any) -> None:
    """Guest session: no agents, no boardroom, no setup or wow agents, WebSocket events filtered. Called first
    thing in OrionDaemon.start()."""
    if not guest_mode():
        return
    log.info("Guest session: Orion runs without agents, messages or approvals")
    agents = getattr(daemon, "agents", None)
    if isinstance(agents, dict):
        for k in agents:
            agents[k] = False

    async def _off(*_a: Any, **_k: Any) -> None:
        return None
    for name in ("board", "setup", "wow"):
        part = getattr(daemon, name, None)
        if part is not None:
            try:
                part.start = _off
            except AttributeError:
                pass
    original = daemon.dispatch

    async def dispatch(client: Any, msg: dict[str, Any]) -> None:
        if not ws_allowed(msg.get("event")):
            await daemon.bus.send(client, {"event": "LOG", "level": "warn",
                                           "message": "That isn't available in a guest session."})
            return
        await original(client, msg)
    daemon.dispatch = dispatch


def make_router(get_daemon: Callable[[], Any]):
    from fastapi import APIRouter

    router = APIRouter(tags=["everyone"])

    @router.get("/access")
    async def access() -> Any:
        return read_access()

    @router.get("/guest")
    async def guest() -> Any:
        return {"guest": guest_mode()}

    return router


def install(app: Any, get_daemon: Callable[[], Any]) -> None:
    """The routes plus the guest gate in front of every HTTP route (one line in main.py)."""
    from fastapi import Request
    from fastapi.responses import JSONResponse

    app.include_router(make_router(get_daemon))

    @app.middleware("http")
    async def guest_gate(request: Request, call_next):  # noqa: ANN001
        if guest_mode() and not http_allowed(request.method, request.url.path):
            return JSONResponse({"error": "not available in a guest session"}, status_code=403)
        return await call_next(request)
