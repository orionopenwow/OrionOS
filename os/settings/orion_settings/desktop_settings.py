"""Settings data for "Keyboard & windows" and "This PC's hardware" (H13, cheap feature 2). No GTK here.

Everything goes through Orion's own commands: orion-keyboard, orion-windows, orion-check-hardware (os/lab/bin), and
the daemon's POST /hardware/report/send for the phone card.
"""
from __future__ import annotations

import re
from typing import Any, Optional

from . import parsers as P
from .backend import API, plain_error

LAYOUT_OK = re.compile(r"^[a-z][a-z0-9_-]{0,15}(:[A-Za-z0-9_-]{1,31})?$")
PILL = {"ok": ("works", "ok"), "warn": ("note", "warn"), "fail": ("fix first", "warn"), "na": ("not here", "off")}
ICONS = {"gpu": "video-display-symbolic", "nvidia": "video-display-symbolic", "wifi": "network-wireless-symbolic",
         "bluetooth": "bluetooth-active-symbolic", "speakers": "audio-speakers-symbolic",
         "mic": "audio-input-microphone-symbolic", "webcam": "camera-web-symbolic",
         "suspend": "weather-clear-night-symbolic", "battery": "battery-good-symbolic",
         "scaling": "zoom-in-symbolic", "touchpad": "input-touchpad-symbolic"}


def keyboard(sys: Any) -> dict[str, Any]:
    if not sys.has("orion-keyboard"):
        return {"available": False}
    cur = P.parse_json_maybe(sys.r.run(["orion-keyboard", "current", "--json"], timeout=8).out) or {}
    items = P.parse_json_maybe(sys.r.run(["orion-keyboard", "list", "--all", "--json"], timeout=8).out) or []
    choices = [(str(i["value"]), str(i["name"])) for i in items if isinstance(i, dict) and i.get("value")]
    value = cur.get("layout", "") + (":" + cur["variant"] if cur.get("variant") else "")
    if value and value not in dict(choices):
        choices.insert(0, (value, cur.get("name") or value))
    return {"available": True, "current": value, "name": cur.get("name", value), "choices": choices}


def set_keyboard(sys: Any, value: str) -> tuple[bool, str]:
    if not LAYOUT_OK.match(value or ""):
        return False, "That keyboard layout can't be used."
    r = sys.r.run(["orion-keyboard", "set", value], timeout=10)
    return (True, r.out.strip() or "Keyboard changed.") if r.ok else (False, plain_error(r, "change the keyboard"))


def windows(sys: Any) -> dict[str, Any]:
    if not sys.has("orion-windows"):
        return {"available": False}
    on = sys.r.run(["orion-windows", "buttons", "status"], timeout=5).out.strip() != "off"
    return {"available": True, "buttons": on, "shortcuts": sys.has("orion-shortcuts")}


def set_buttons(sys: Any, on: bool) -> tuple[bool, str]:
    r = sys.r.run(["orion-windows", "buttons", "on" if on else "off"], timeout=10)
    return (True, r.out.strip()) if r.ok else (False, plain_error(r, "change the window buttons"))


def gravity(sys: Any) -> dict[str, Any]:
    """Gravity mode and the optional Hyprland session (orion-gravity; after-1.0 plan §3)."""
    if not sys.has("orion-gravity"):
        return {"available": False}
    st = P.parse_json_maybe(sys.r.run(["orion-gravity", "status", "--json"], timeout=8).out)
    if not isinstance(st, dict):
        return {"available": False}
    return {"available": True, "on": bool(st.get("wanted")), "active": bool(st.get("active")),
            "reason": str(st.get("reason") or ""), "hyprland": st.get("session") == "hyprland",
            "installed": bool(st.get("hyprland_installed")), "compositor": str(st.get("compositor") or "")}


def set_gravity(sys: Any, on: bool) -> tuple[bool, str]:
    r = sys.r.run(["orion-gravity", "on" if on else "off"], timeout=10)
    return (True, r.out.strip()) if r.ok else (False, plain_error(r, "change Gravity mode"))


def set_hyprland(sys: Any, on: bool) -> tuple[bool, str]:
    r = sys.r.run(["orion-gravity", "session", "hyprland" if on else "sway"], timeout=10)
    return (True, r.out.strip()) if r.ok else (False, plain_error(r, "change the desktop for the next login"))


def hardware(sys: Any) -> Optional[dict[str, Any]]:
    if not sys.has("orion-check-hardware"):
        return None
    data = P.parse_json_maybe(sys.r.run(["orion-check-hardware", "--json"], timeout=60).out)
    return data if isinstance(data, dict) and isinstance(data.get("rows"), list) else None


def send_hardware(sys: Any) -> tuple[bool, str]:
    res = sys.r.http("POST", f"{API}/hardware/report/send", {}, timeout=60)
    if not isinstance(res, dict):
        return False, "Orion's brain isn't answering, so it couldn't be sent."
    if res.get("sent"):
        return True, "Sent to your phone."
    return False, str(res.get("message") or "No phone is paired yet (Settings > Orion > Pair my phone).")


def headline(rep: dict[str, Any]) -> tuple[str, str, str]:
    """(title, subtitle, pill kind) for the top row."""
    if rep.get("safe"):
        notes = rep.get("warnings") or 0
        sub = "Everything important works." + (f" {notes} thing{'s' if notes != 1 else ''} worth knowing below."
                                               if notes else "")
        return rep.get("headline", "Safe to install on this PC"), sub, "ok"
    return rep.get("headline", "Not ready yet"), "Each red row says how to fix it.", "warn"
