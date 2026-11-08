"""Settings > Privacy > Camera presence, the part without GTK (so it can be tested): reads and changes the webcam
presence and gestures through the daemon's /presence routes (apps/daemon/presence.py, after-1.0 plan §6).

Off by default. Everything runs on this computer; pictures are never saved or sent. The hardware camera switch above
it (orion-settings-camera) still wins: with the camera off there, presence can't start."""
from __future__ import annotations

from typing import Any, Optional

from .backend import API, Runner

NOT_RUNNING = "Orion's brain isn't answering. Start it to change camera presence."

# (config key, title, subtitle) for the switches under "Camera presence"
SWITCHES: list[tuple[str, str, str]] = [
    ("lock_when_away", "Lock when I walk away", "Locks the screen after you've been gone for a while"),
    ("greet", "Greet me when I come back", "A short hello on the screen"),
    ("pause_media", "Pause media when I look away", "Starts again when you look back"),
    ("gestures", "Hand gestures", "Hold a hand up for half a second first. Open palm freezes Orion."),
    ("off_on_battery", "Switch off on battery", "Saves power on a laptop"),
    ("start_with_orion", "Start with Orion", "Off means the camera always starts off"),
]


def load(r: Runner) -> Optional[dict[str, Any]]:
    st = r.http("GET", f"{API}/presence")
    return st if isinstance(st, dict) else None


def summary(st: Optional[dict[str, Any]]) -> str:
    if st is None:
        return NOT_RUNNING
    if st.get("missing"):
        return "Needs the camera add-on: pip install " + " ".join(st["missing"])
    if st.get("on"):
        if st.get("paused"):
            return st["paused"]
        return f"On. Real screen time today: {st.get('screen_time_today_min', 0)} min. Nothing is recorded."
    if st.get("error"):
        return st["error"]
    return "Off. Turn it on to lock when you leave and use hand gestures. Nothing is recorded or sent."


def set_on(r: Runner, on: bool) -> tuple[bool, str]:
    st = r.http("POST", f"{API}/presence", {"action": "on" if on else "off"})
    if not isinstance(st, dict):
        return False, NOT_RUNNING
    if on and not st.get("on"):
        return False, st.get("error") or "Camera presence couldn't start."
    return True, "Camera presence is on." if on else "Camera presence is off."


def set_option(r: Runner, key: str, value: bool) -> tuple[bool, str]:
    if key not in {k for k, _t, _s in SWITCHES}:
        return False, "Unknown setting."
    st = r.http("POST", f"{API}/presence/config", {key: bool(value)})
    return (True, "Saved.") if isinstance(st, dict) else (False, NOT_RUNNING)
