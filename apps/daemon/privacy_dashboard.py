"""One privacy dashboard (OrionOS 1.0 plan, K. "Privacy dashboard: what Orion can see, hear and touch").

Every sensor or capture feature Orion has, in one list, with its state read from the module that owns it, and a
one-tap off for each, plus "Turn everything off". The owning modules are not changed: this file only reads their
state and calls the off switches they already have.

    GET  /privacy/dashboard      {"sensors": [...], "on": n, "words": "..."}
    POST /privacy/off            {"id": "hotmic"}           switch one off
    POST /privacy/off-all        {}                         switch every one off

Each sensor row: id, label, what (plain words), on (bool), available (bool), can_off (bool), how_on (where to turn
it back on: turning things on stays in each feature's own settings, with its own consent screen).

Turning on is deliberately not offered here: this page only ever makes Orion see and hear less.
"""
from __future__ import annotations

import asyncio
import inspect
import logging
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional

from pydantic import BaseModel

log = logging.getLogger("orion.privacy")

CAMERA_STATE = Path(os.environ.get("ORION_CAMERA_STATE", "/etc/orion/camera"))
CAMERA_HELPER = Path(os.environ.get("ORION_CAMERA_HELPER", "/opt/orion/settings/helpers/orion-settings-camera"))


@dataclass
class Sensor:
    id: str
    label: str
    what: str
    how_on: str
    state: Callable[[Any], Optional[bool]]          # True on, False off, None not on this computer
    off: Optional[Callable[[Any], Any]] = None       # None: nothing to switch off (always off)


async def _maybe_await(x: Any) -> Any:
    return await x if inspect.isawaitable(x) else x


# ------------------------------------------------------------------ state readers (read only, never change)
def _screen(d: Any) -> Optional[bool]:
    agents = getattr(d, "agents", None)
    if not isinstance(agents, dict):
        return None
    cap = getattr(d, "capture", None)
    on = bool(agents.get("system_capturer"))
    if cap is not None and hasattr(cap, "paused"):
        on = on and not cap.paused()
    return on


def _proactive(d: Any) -> Optional[bool]:
    agents = getattr(d, "agents", None)
    return bool(agents.get("proactive_observer")) if isinstance(agents, dict) else None


def _guide(d: Any) -> Optional[bool]:
    g = getattr(d, "guide", None)
    return bool(g.active()) if g is not None else None


def _hotmic(d: Any) -> Optional[bool]:
    hm = getattr(d, "hotmic", None)
    return bool(hm.listening) if hm is not None else None


def _wake(d: Any) -> Optional[bool]:
    hm = getattr(d, "hotmic", None)
    if hm is None:
        return None
    on = bool(hm.config().get("wake_word"))
    wy = getattr(d, "wyoming", None)
    return on or bool(wy is not None and wy.running)


def _voice_typing(d: Any) -> Optional[bool]:
    stt = getattr(d, "stt", None)
    if stt is None:
        return False
    alive = stt.is_alive() if hasattr(stt, "is_alive") else True
    paused = getattr(stt, "_paused", None)
    return bool(alive and not (paused is not None and paused.is_set()))


def camera_state(path: Path = CAMERA_STATE) -> Optional[bool]:
    """OrionOS: the camera driver switch (os/settings/helpers/orion-settings-camera). None elsewhere."""
    if not sys.platform.startswith("linux") or not path.parent.exists():
        return None
    try:
        return path.read_text(encoding="utf-8").strip() == "on"
    except OSError:
        return False     # the helper hasn't run yet: OrionOS starts with the camera off


def _camera(_d: Any) -> Optional[bool]:
    return camera_state()


def _recorder(d: Any) -> Optional[bool]:
    seen = False
    rec = getattr(getattr(d, "ext", None), "recorder", None)
    on = False
    if rec is not None:
        seen = True
        on = bool(getattr(rec, "recording", False))
    skills = getattr(d, "skills", None)
    if skills is not None and hasattr(skills, "recordings"):
        seen = True
        on = on or any(v.get("active") for v in skills.recordings.values())
    return on if seen else None


def _inbox(d: Any) -> Optional[bool]:
    pp = getattr(d, "phone_pair", None)
    if pp is None:
        return None
    return bool(pp.settings().get("inbox_mirror"))


def _never(_d: Any) -> Optional[bool]:
    return False


# ------------------------------------------------------------------ off switches (the owners' own)
async def _screen_off(d: Any) -> None:
    await _maybe_await(d.set_agent("system_capturer", False))


async def _proactive_off(d: Any) -> None:
    await _maybe_await(d.set_agent("proactive_observer", False))


async def _guide_off(d: Any) -> None:
    await _maybe_await(d.guide.stop("privacy dashboard"))


async def _hotmic_off(d: Any) -> None:
    await asyncio.to_thread(d.hotmic.stop, True)


async def _wake_off(d: Any) -> None:
    d.hotmic.set_config(wake_word=False)
    wy = getattr(d, "wyoming", None)
    if wy is not None and wy.running:
        await _maybe_await(wy.stop())


async def _voice_typing_off(d: Any) -> None:
    d.stt.pause()


def camera_off(helper: Path = CAMERA_HELPER) -> None:
    """The same narrow root helper Settings uses; polkit lets the person at the computer run it without a
    password (os/settings/polkit/50-orion-settings.rules)."""
    if not helper.exists() or not shutil.which("pkexec"):
        raise RuntimeError("Turn the camera off in Settings > Privacy.")
    r = subprocess.run(["pkexec", str(helper), "off"], capture_output=True, text=True, timeout=60)
    if r.returncode != 0:
        raise RuntimeError((r.stderr or r.stdout or "The camera switch didn't work.").strip()[:200])


async def _camera_off(_d: Any) -> None:
    await asyncio.to_thread(camera_off)


async def _recorder_off(d: Any) -> None:
    rec = getattr(getattr(d, "ext", None), "recorder", None)
    if rec is not None and getattr(rec, "recording", False):
        await asyncio.to_thread(rec.stop)
    skills = getattr(d, "skills", None)
    if skills is not None and any(v.get("active") for v in getattr(skills, "recordings", {}).values()):
        await _maybe_await(skills.stop_recording())


async def _inbox_off(d: Any) -> None:
    d.phone_pair.set_inbox_mirror(False)


SENSORS: list[Sensor] = [
    Sensor("screen", "Screen reading", "Reads the text on your screen, on this computer, so Orion can help with "
           "what you're doing. Password managers, banking and private windows are always skipped.",
           "Settings > Privacy > Orion reads your screen", _screen, _screen_off),
    Sensor("proactive", "Tips from what's on screen", "Suggests things based on the window you're in.",
           "Orion's panel > Agents", _proactive, _proactive_off),
    Sensor("guide", "Guide Mode", "Looks at the screen only while it walks you through a task, to put the ring "
           "on the next button.", "Say “Orion, show me how to …”", _guide, _guide_off),
    Sensor("hotmic", "Hot mic", "Listens for your voice all the time and only acts on you saying “Orion, …”. "
           "Nothing is recorded or saved. Ctrl+Alt+M mutes it.", "orion-ctl mic on", _hotmic, _hotmic_off),
    Sensor("wake", "Wake word “Orion”", "A small model on this computer listens for the word “Orion” before "
           "anything else is checked.", "orion-ctl mic wake on", _wake, _wake_off),
    Sensor("voice_typing", "Voice typing", "Turns speech into text on this computer while Orion's window is in "
           "use.", "Restart Orion", _voice_typing, _voice_typing_off),
    Sensor("camera", "Camera and presence", "The camera itself. Orion doesn't watch for you with it; this switch "
           "stops every app from opening it.", "Settings > Privacy > Camera", _camera, _camera_off),
    Sensor("recorder", "Recorders", "Watch-and-learn recording of clicks and typing, and browser skill "
           "recording. Only between your start and stop.", "Say “Orion, watch what I do”", _recorder,
           _recorder_off),
    Sensor("inbox_mirror", "Inbox mirror", "Copies your phone's messages to this computer so Orion can draft "
           "replies. Never sends anything.", "Settings > Orion > Mirror inbox to my PC", _inbox, _inbox_off),
    Sensor("telemetry", "Usage data", "Orion sends no usage data anywhere. There is nothing to turn on.", "",
           _never, None),
    Sensor("crash_reports", "Crash reports", "None are sent. Crashes are fixed on this computer by the crash "
           "fixer, which only proposes fixes.", "", _never, None),
]
BY_ID = {s.id: s for s in SENSORS}


class PrivacyDashboard:
    def __init__(self, daemon: Any, sensors: Optional[list[Sensor]] = None) -> None:
        self.d = daemon
        self.sensors = sensors or SENSORS
        self.by_id = {s.id: s for s in self.sensors}

    def row(self, s: Sensor) -> dict[str, Any]:
        try:
            on = s.state(self.d)
        except Exception:
            log.debug("privacy state %s failed", s.id, exc_info=True)
            on = None
        return {"id": s.id, "label": s.label, "what": s.what, "how_on": s.how_on,
                "on": bool(on), "available": on is not None, "can_off": bool(on) and s.off is not None}

    def snapshot(self) -> dict[str, Any]:
        rows = [self.row(s) for s in self.sensors]
        n = sum(1 for r in rows if r["on"])
        words = "Nothing is watching or listening." if n == 0 else \
            f"{n} thing{'s are' if n != 1 else ' is'} on: " + ", ".join(r["label"] for r in rows if r["on"]) + "."
        return {"sensors": rows, "on": n, "words": words}

    async def off(self, sid: str) -> dict[str, Any]:
        s = self.by_id.get(sid)
        if s is None:
            raise KeyError(sid)
        if s.off is None:
            return {"ok": True, "id": sid, "message": f"{s.label}: already off."}
        if not self.row(s)["on"]:
            return {"ok": True, "id": sid, "message": f"{s.label} is off."}
        try:
            await _maybe_await(s.off(self.d))
        except Exception as exc:
            return {"ok": False, "id": sid, "message": str(exc)[:200] or f"Couldn't turn {s.label} off."}
        await self._tell(f"Privacy dashboard: {s.label} turned off")
        return {"ok": True, "id": sid, "message": f"{s.label} is off."}

    async def off_all(self) -> dict[str, Any]:
        results = []
        for s in self.sensors:
            if s.off is not None and self.row(s)["on"]:
                results.append(await self.off(s.id))
        failed = [r for r in results if not r["ok"]]
        msg = "Everything is off." if not failed else \
            "Turned off what I could. Still on: " + "; ".join(r["message"] for r in failed)
        return {"ok": not failed, "results": results, "message": msg, **self.snapshot()}

    async def _tell(self, line: str) -> None:
        bus = getattr(self.d, "bus", None)
        try:
            if bus is not None:
                await bus.log(line)
                await bus.publish({"event": "PRIVACY_STATE", **self.snapshot()})
        except Exception:
            log.debug("privacy publish failed", exc_info=True)


class OffReq(BaseModel):
    id: str


def make_router(get_daemon: Callable[[], Any]):
    from fastapi import APIRouter, HTTPException

    router = APIRouter()

    def dash() -> PrivacyDashboard:
        d = get_daemon()
        pd = getattr(d, "privacy_dashboard", None)
        if pd is None:
            pd = PrivacyDashboard(d)
            try:
                d.privacy_dashboard = pd
            except Exception:
                pass
        return pd

    @router.get("/privacy/dashboard")
    async def dashboard() -> dict[str, Any]:
        return dash().snapshot()

    @router.post("/privacy/off")
    async def off(req: OffReq) -> dict[str, Any]:
        try:
            return {**(await dash().off(req.id)), **dash().snapshot()}
        except KeyError:
            raise HTTPException(404, "No such switch.")

    @router.post("/privacy/off-all")
    async def off_all() -> dict[str, Any]:
        return await dash().off_all()

    return router

