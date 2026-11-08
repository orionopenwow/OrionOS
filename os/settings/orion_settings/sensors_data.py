"""Data for Settings > Privacy dashboard (sensors_page.py): the same list as the HUD's privacy window, from Orion's
brain (GET /privacy/dashboard, apps/daemon/privacy_dashboard.py). No GTK here, so it's tested anywhere.

When the brain isn't answering, the page still shows what Settings can read itself (camera, screen reading), so
the camera can always be switched off from here.
"""
from __future__ import annotations

import os
from typing import Any, Optional

API = os.environ.get("ORION_API", "http://127.0.0.1:9120")


def dashboard(r: Any, local: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    res = r.http("GET", f"{API}/privacy/dashboard")
    if isinstance(res, dict) and isinstance(res.get("sensors"), list):
        rows = [s for s in res["sensors"] if isinstance(s, dict) and s.get("id")]
        return {"brain": True, "sensors": rows, "on": sum(1 for s in rows if s.get("on")),
                "words": str(res.get("words") or "")}
    loc = local or {}
    rows = [
        {"id": "camera", "label": "Camera and presence", "what": "The camera itself.", "on": bool(loc.get("camera")),
         "available": True, "can_off": bool(loc.get("camera")), "how_on": "Settings > Privacy > Camera"},
        {"id": "screen", "label": "Screen reading", "what": "Orion reading the text on screen (starts with Orion).",
         "on": bool(loc.get("capture")), "available": True, "can_off": False,
         "how_on": "Settings > Privacy > Orion reads your screen"},
    ]
    return {"brain": False, "sensors": rows, "on": sum(1 for s in rows if s["on"]),
            "words": "Orion's brain isn't answering, so only the camera and the screen-reading setting are shown."}


def turn_off(r: Any, sid: str) -> tuple[bool, str]:
    res = r.http("POST", f"{API}/privacy/off", {"id": sid}, timeout=60)
    if not isinstance(res, dict):
        return False, "Orion's brain isn't answering."
    return bool(res.get("ok")), str(res.get("message") or "")


def turn_all_off(r: Any) -> tuple[bool, str]:
    res = r.http("POST", f"{API}/privacy/off-all", {}, timeout=120)
    if not isinstance(res, dict):
        return False, "Orion's brain isn't answering."
    return bool(res.get("ok")), str(res.get("message") or "")


def state_words(s: dict[str, Any]) -> tuple[str, str]:
    """(pill text, pill kind) for one row."""
    if not s.get("available", True):
        return "not here", ""
    return ("on", "warn") if s.get("on") else ("off", "ok")
