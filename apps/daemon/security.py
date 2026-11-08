"""Local-only access control for the daemon (V2 hardening, review item B1).

Three checks guard every HTTP call and the WebSocket (main.py):
  1. Host header must be 127.0.0.1 / localhost / [::1] (with any port). A web page that points its own domain
     name at 127.0.0.1 ("DNS rebinding") still sends its own name here, so it is refused.
  2. Origin (below): web pages can't forge it.
  3. The per-start token (api_token.py): only programs that can read the user's private token file (the HUD,
     orion-ctl, Settings, the MCP server, ...) or a paired browser extension get in. Public without it: GET /health
     (answers only {"ok": true}) and the tripwire alarm (tripwires.py), which strangers are meant to trip.

Browsers attach an `Origin` header to every WebSocket handshake and cross-site HTTP request,
and web pages cannot forge it. Without this check any website the user visits could open
ws://localhost:9120, read mirrored DMs, message contacts as the user, or approve a
self-evolve patch. Clients without an Origin (the VS Code companion, scripts) are local
processes and are allowed, as they could already act on the machine anyway.
"""
from __future__ import annotations

import os
from typing import Optional

from config import settings

DEFAULT_ALLOWED_ORIGINS = (
    "http://localhost:1420",   # Vite dev server / launch.bat web HUD
    "http://127.0.0.1:1420",
    "tauri://localhost",       # Tauri (macOS / Linux)
    "http://tauri.localhost",  # Tauri v2 on Windows
    "https://tauri.localhost",
)
EXTENSION_SCHEMES = ("chrome-extension://", "moz-extension://", "edge-extension://")

# Events that act on the user's behalf. Browser-extension clients only ever need to
# report tabs/DMs and confirm injected replies, so they may never send these.
PRIVILEGED_EVENTS = frozenset({
    "USER_PROMPT", "VOICE_TRANSCRIPT", "AGENT_TOGGLE", "SET_PERSONALITY", "SET_TTS_VOICE",
    "EXECUTE_SCREEN_ACTION", "EXECUTE_SCREEN_WORKFLOW", "SEND_TELEGRAM", "DISCORD_SEND_RESPONSE",
    "SAVE_SKILL", "SPEAK", "MEMORY_QUERY", "CHROME_SCRAPE_REQUEST", "ROUTINES_REQUEST",
    "APPROVAL_DECISION", "PATCH_DECISION", "SELF_EVOLVE_REQUEST", "COMMS_REPLY",
    "COMMS_DRAFT_REQUEST", "COMMS_MARK_READ", "PACING_ACK", "CONSOLIDATE_REQUEST", "EXPORT_TRAINING",
    "BOARDROOM_SIMULATE",
})


def allowed_origins() -> set[str]:
    extra = {o.strip().lower().rstrip("/") for o in settings.extra_allowed_origins if o.strip()}
    return {o.lower() for o in DEFAULT_ALLOWED_ORIGINS} | extra


def origin_allowed(origin: Optional[str]) -> bool:
    if origin is None or origin == "":
        return True
    o = origin.strip().lower().rstrip("/")
    if o.startswith(EXTENSION_SCHEMES):
        return True
    return o in allowed_origins()


def is_extension_origin(origin: Optional[str]) -> bool:
    return bool(origin) and origin.strip().lower().startswith(EXTENSION_SCHEMES)


# ------------------------------------------------------------------ Host allowlist (DNS rebinding)
LOCAL_HOSTS = frozenset({"127.0.0.1", "localhost", "[::1]", "::1"})


def allowed_hosts() -> set[str]:
    extra = {h.strip().lower() for h in os.environ.get("ORION_ALLOWED_HOSTS", "").split(",") if h.strip()}
    return set(LOCAL_HOSTS) | extra


def host_name(host_header: Optional[str]) -> str:
    """'LOCALHOST:9120' -> 'localhost', '[::1]:9120' -> '[::1]'."""
    h = (host_header or "").strip().lower()
    if h.startswith("["):
        end = h.find("]")
        return h[: end + 1] if end != -1 else h
    if h.count(":") == 1:
        h = h.split(":", 1)[0]
    return h.rstrip(".")


def host_allowed(host_header: Optional[str]) -> bool:
    return host_name(host_header) in allowed_hosts()


# ------------------------------------------------------------------ routes open without the token
def public_route(method: str, path: str) -> bool:
    """Routes that answer without the token: the bare health probe and the tripwire alarm."""
    method = method.upper()
    if path == "/health" and method in ("GET", "HEAD"):
        return True
    return path.startswith("/tripwire/")


def skips_host_check(path: str) -> bool:
    """The tripwire alarm must ring whatever name the intruder used."""
    return path.startswith("/tripwire/")
