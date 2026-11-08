"""Presence: the webcam knows when you're there, and reads a few hand gestures (after-1.0 plan §6, Phase 5).

OFF BY DEFAULT, and off again every time Orion starts (unless you tick "Start with Orion"). One tap on the camera
icon in the HUD, Ctrl+Alt+C on OrionOS, or `orion-ctl camera toggle` turns it on or off, silently: no sound, no
pop-up. While it is on the HUD shows a camera light, and the core signals carry `camera: true` so the black hole and
the phone show it too.

Privacy:
  - 100% on this computer: OpenCV reads a small (320x240) picture, MediaPipe finds hand and face points in it.
  - Frames are never saved, sent, logged or kept: each one becomes a few numbers (gestures.py) and is dropped
    before the next is read. Only the settings and a per-day screen-time number are written to disk.
  - On OrionOS the camera also has a hardware switch (Settings > Privacy > Camera, orion-settings-camera). When that
    is off, presence can't open the camera and says so.

Presence: locks the screen when you walk away (OrionOS: orion-lock, else loginctl lock-session; Windows:
LockWorkStation), wakes the screen and greets you when you sit back down, can pause media while you look away, and
counts real screen time, which keeps the focus arc (pacing_tracker) going while you read or watch.

Gestures (gestures.py; only after a hand is held in the gesture zone for half a second):
    point + pinch       move the pointer, click
    open palm, hold     the kill switch: freezes Orion and pauses all agents (freeze.py; unfreezing needs the phone)
    swipe left / right  next / previous desktop (Sway workspaces; Win+Ctrl+arrows on Windows)
    draw a rectangle    a new desktop (two fingers out, like a pen)
    grab and throw      left / right: move the window to that desktop; down: "into the black hole" (minimise)
    two hands           spread = zoom the HUD in / open the panel, squeeze = zoom out / close it (the HUD acts)
    thumbs up           approve the current SMALL card only: information cards. Never boardroom decisions, never
                        anything about money, never sending messages (small_card_refusal below; tested)

CPU budget: low frame rate (3 a second with nobody's hand up, 10 while a hand is in view), the smallest MediaPipe
models, the camera is closed while a full-screen game or video is in front, and presence switches itself off on
battery if you choose that (on by default).

Optional add-on: `pip install opencv-python-headless mediapipe` (requirements-optional.txt). Without it the switch
explains what's missing and nothing else changes.

HTTP (local, token):  GET /presence   POST /presence {action: on|off|toggle}   POST /presence/config {...}
                      GET /presence/calibration   POST /presence/calibrate {action: start|finish|reset}
                      POST /presence/current-card {id}   (the HUD says which card is in front)
"""
from __future__ import annotations

import asyncio
import importlib.util
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable, Optional

from fastapi import APIRouter, Request

import gestures as G

log = logging.getLogger("orion.presence")

DEFAULTS: dict[str, Any] = {
    "start_with_orion": False,   # the camera always starts off unless you choose this
    "lock_when_away": True,
    "away_after_s": 30,
    "greet": True,
    "greet_aloud": False,
    "pause_media": False,
    "gestures": True,
    "pointer": True,
    "fps": 10,
    "idle_fps": 3,
    "pause_in_fullscreen": True,
    "off_on_battery": True,
    "zone": list(G.DEFAULT_ZONE),
    "camera_index": 0,
}
WIDTH, HEIGHT = 320, 240
MARK_ACTIVITY_EVERY_S = 15.0
BATTERY_EVERY_S = 30.0
CARD_MEMORY_S = 120.0
CAMERA_SWITCH = "/etc/orion/camera"

# ------------------------------------------------------------------------------------- thumbs up: small cards only
SMALL_KINDS = frozenset({"backup_drill", "perf_regression", "security_report"})   # information cards: "seen"
NEVER_KINDS = frozenset({"boardroom", "self_evolve", "grimoire_run", "skill_step", "devlog", "get_apps",
                         "app_install", "secret_shield", "safety.unfreeze", "discord_prompt", "comms_reply",
                         "outreach", "stardust", "payment"})
MONEY_FIELDS = ("amount", "cost", "price", "usd", "dollars", "spend", "budget", "payment", "fee", "dust", "value_usd")
MONEY_WORDS = re.compile(
    r"[$€£¥]|\b(money|pay|paid|paying|payment|payout|price|cost|costs|spend|spent|purchase|buy|buying|refund|"
    r"invoice|wallet|donat\w*|bounty|stardust|dust|crypto|bank|transfer|subscription|billing|usd|eur|gbp|"
    r"funds?|cash|tip|tips|royalt\w*|sell|sale)\b", re.I)


def small_card_refusal(item: Any) -> Optional[str]:
    """None when a thumbs up may approve this card; otherwise the plain reason it may not."""
    if not isinstance(item, dict):
        return "No card is showing."
    if str(item.get("status") or "pending") != "pending":
        return "That card is already decided."
    kind = str(item.get("kind") or "")
    if kind in NEVER_KINDS or kind.startswith("boardroom") or item.get("signed") or item.get("needs_sign"):
        return "Boardroom decisions are signed on your phone or the boardroom, never with a gesture."
    if any(item.get(k) not in (None, "", 0, 0.0, False) for k in MONEY_FIELDS):
        return "Anything about money needs your phone's fingerprint, not a gesture."
    text = " ".join(str(item.get(k) or "") for k in ("title", "summary", "category", "draft", "detail", "details",
                                                     "text", "reason", "original", "body"))
    if MONEY_WORDS.search(text):
        return "Anything about money needs your phone's fingerprint, not a gesture."
    if str(item.get("risk") or "low").lower() != "low" or item.get("sensitive"):
        return "A thumbs up only approves small cards. Tap this one yourself."
    if kind not in SMALL_KINDS:
        return "A thumbs up only approves small information cards. Tap this one yourself."
    return None


def missing() -> list[str]:
    """Which optional parts aren't installed (checked without importing them)."""
    out = []
    for mod, pkg in (("cv2", "opencv-python-headless"), ("mediapipe", "mediapipe")):
        try:
            if importlib.util.find_spec(mod) is None:
                out.append(pkg)
        except (ImportError, ValueError):
            out.append(pkg)
    return out


def camera_switch(path: str = CAMERA_SWITCH) -> Optional[str]:
    """OrionOS's hardware camera switch: 'on', 'off', or None elsewhere."""
    try:
        return Path(path).read_text(encoding="utf-8").strip() or None
    except OSError:
        return None


def greeting(hour: Optional[int] = None) -> str:
    hour = time.localtime().tm_hour if hour is None else hour
    return ("Morning" if 4 <= hour < 12 else "Evening") + ", welcome back."


# ============================================================================================ camera + models
class OpenCVCamera:
    """The webcam, small and mirrored. read() returns an RGB frame that the caller must not keep."""

    def __init__(self, index: int = 0) -> None:
        import cv2  # presence add-on
        self.cv2 = cv2
        api = getattr(cv2, "CAP_DSHOW", 0) if sys.platform == "win32" else 0
        self.cap = cv2.VideoCapture(int(index), api) if api else cv2.VideoCapture(int(index))
        if not self.cap or not self.cap.isOpened():
            raise RuntimeError("The camera couldn't be opened. Is it switched on and not in use by another app?")
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, WIDTH)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, HEIGHT)

    def read(self) -> Any:
        ok, frame = self.cap.read()
        if not ok:
            return None
        return self.cv2.cvtColor(self.cv2.flip(frame, 1), self.cv2.COLOR_BGR2RGB)

    def close(self) -> None:
        try:
            self.cap.release()
        except Exception:
            pass


class MediaPipeDetector:
    """Frame -> (hands, face): up to two lists of 21 (x, y, z) points, and the nose and eye corners."""

    def __init__(self) -> None:
        import mediapipe as mp  # presence add-on
        sol = getattr(mp, "solutions", None)
        if sol is None:
            raise RuntimeError("This MediaPipe version has no built-in hand model. Install mediapipe 0.10.x.")
        self.hands = sol.hands.Hands(static_image_mode=False, max_num_hands=2, model_complexity=0,
                                     min_detection_confidence=0.6, min_tracking_confidence=0.5)
        self.face = sol.face_mesh.FaceMesh(static_image_mode=False, max_num_faces=1, refine_landmarks=False,
                                           min_detection_confidence=0.5, min_tracking_confidence=0.5)
        self.n = 0
        self.last_face: Optional[dict[str, tuple[float, float]]] = None

    def __call__(self, rgb: Any) -> tuple[list[list[tuple[float, float, float]]], Optional[dict[str, Any]]]:
        try:
            rgb.flags.writeable = False
        except AttributeError:
            pass
        hr = self.hands.process(rgb)
        hands = [[(p.x, p.y, p.z) for p in h.landmark] for h in (getattr(hr, "multi_hand_landmarks", None) or [])]
        self.n += 1
        if self.n % 2 == 1:   # the face every other frame: presence doesn't need more
            fr = self.face.process(rgb)
            faces = getattr(fr, "multi_face_landmarks", None) or []
            if faces:
                lm = faces[0].landmark
                self.last_face = {"nose": (lm[1].x, lm[1].y), "left_eye": (lm[33].x, lm[33].y),
                                  "right_eye": (lm[263].x, lm[263].y)}
            else:
                self.last_face = None
        return hands, self.last_face

    def close(self) -> None:
        for m in (self.hands, self.face):
            try:
                m.close()
            except Exception:
                pass


# ============================================================================================ what gestures do
class DesktopActions:
    """Desktops, windows, locking, media and the pointer, on OrionOS (Sway) and Windows. `run` and `keys` are
    replaceable for tests."""

    def __init__(self, platform: str = sys.platform, run: Optional[Callable[[list[str]], bool]] = None,
                 spawn: Optional[Callable[[list[str]], bool]] = None,
                 keys: Optional[Callable[[tuple[str, ...]], bool]] = None,
                 which: Callable[[str], Optional[str]] = shutil.which) -> None:
        self.platform = platform
        self.run = run or self._run
        self.spawn = spawn or self._spawn
        self.keys = keys or self._win_keys
        self.which = which
        self.paused_media = False

    # ------------------------------------------------------------ plumbing
    @staticmethod
    def _run(args: list[str]) -> bool:
        try:
            return subprocess.run(args, capture_output=True, timeout=3).returncode == 0
        except (OSError, subprocess.SubprocessError):
            return False

    @staticmethod
    def _out(args: list[str]) -> str:
        try:
            r = subprocess.run(args, capture_output=True, text=True, timeout=3)
            return r.stdout if r.returncode == 0 else ""
        except (OSError, subprocess.SubprocessError):
            return ""

    @staticmethod
    def _spawn(args: list[str]) -> bool:
        try:
            subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             start_new_session=True)
            return True
        except OSError:
            return False

    @staticmethod
    def _win_keys(combo: tuple[str, ...]) -> bool:
        """Press a key combination on Windows (keybd_event)."""
        try:
            import ctypes
            vk = {"win": 0x5B, "ctrl": 0x11, "left": 0x25, "right": 0x27, "d": 0x44, "down": 0x28}
            codes = [vk[k] for k in combo]
            user32 = ctypes.windll.user32  # type: ignore[attr-defined]
            for c in codes:
                user32.keybd_event(c, 0, 0, 0)
            for c in reversed(codes):
                user32.keybd_event(c, 0, 2, 0)
            return True
        except Exception:
            return False

    def sway(self) -> bool:
        return self.platform.startswith("linux") and bool(os.environ.get("SWAYSOCK")) and bool(self.which("swaymsg"))

    # ------------------------------------------------------------ desktops
    def desktop(self, which: str) -> str:
        """which: next | prev | new"""
        if self.platform == "win32":
            combo = {"next": ("win", "ctrl", "right"), "prev": ("win", "ctrl", "left"), "new": ("win", "ctrl", "d")}
            return "ok" if self.keys(combo[which]) else "Couldn't switch desktops."
        if not self.sway():
            return "Switching desktops works on OrionOS and Windows."
        if which == "new":
            n = self.free_workspace()
            return "ok" if self.run(["swaymsg", "workspace", "number", str(n)]) else "Couldn't make a new desktop."
        step = "next_on_output" if which == "next" else "prev_on_output"
        return "ok" if self.run(["swaymsg", "workspace", step]) else "Couldn't switch desktops."

    def free_workspace(self) -> int:
        try:
            used = {int(w.get("num", -1)) for w in json.loads(self._out(["swaymsg", "-t", "get_workspaces", "-r"])
                                                              or "[]")}
        except (ValueError, TypeError, AttributeError):
            used = set()
        n = 2                       # 1 is the Orion screen
        while n in used:
            n += 1
        return n

    def throw(self, direction: str) -> str:
        if direction in ("down",):
            return self.minimise()
        if direction not in ("left", "right"):
            return "ignored"
        if self.platform == "win32":
            return "Windows has no shortcut to move a window to another desktop. Use Win+Tab and drag it."
        if not self.sway():
            return "Moving windows between desktops works on OrionOS."
        step = "next_on_output" if direction == "right" else "prev_on_output"
        return "ok" if self.run(["swaymsg", "move", "container", "to", "workspace", step]) else \
            "Couldn't move the window."

    def minimise(self) -> str:
        """'Into the black hole': Sway's scratchpad (Super+Shift+minus brings it back), or minimise on Windows."""
        if self.platform == "win32":
            try:
                import ctypes
                u = ctypes.windll.user32  # type: ignore[attr-defined]
                return "ok" if u.ShowWindow(u.GetForegroundWindow(), 6) is not None else "Couldn't minimise."
            except Exception:
                return "Couldn't minimise the window."
        if not self.sway():
            return "Minimising works on OrionOS and Windows."
        return "ok" if self.run(["swaymsg", "move", "scratchpad"]) else "Couldn't minimise the window."

    # ------------------------------------------------------------ lock, wake, media
    def lock(self) -> str:
        if self.platform == "win32":
            try:
                import ctypes
                ctypes.windll.user32.LockWorkStation()  # type: ignore[attr-defined]
                return "ok"
            except Exception:
                return "Couldn't lock the screen."
        if self.which("orion-lock"):
            return "ok" if self.spawn(["orion-lock"]) else "Couldn't lock the screen."
        if self.which("loginctl"):
            return "ok" if self.run(["loginctl", "lock-session"]) else "Couldn't lock the screen."
        return "No screen lock found."

    def wake(self) -> None:
        if self.platform == "win32":
            try:
                import ctypes
                ctypes.windll.user32.mouse_event(1, 0, 0, 0, 0)  # type: ignore[attr-defined]  # a zero move wakes it
            except Exception:
                pass
        elif self.sway():
            self.run(["swaymsg", "output", "*", "power", "on"])

    async def pause_media(self, daemon: Any) -> bool:
        if self.platform.startswith("linux") and self.which("playerctl"):
            if self._out(["playerctl", "status"]).strip() == "Playing" and self.run(["playerctl", "pause"]):
                self.paused_media = True
        elif self.platform == "win32":
            media = getattr(getattr(daemon, "ext", None), "media", None)
            try:
                now = await media.now_playing() if media is not None else None
                if now and now.get("playing") and await media.control("toggle"):
                    self.paused_media = True
            except Exception:
                pass
        return self.paused_media

    async def resume_media(self, daemon: Any) -> bool:
        if not self.paused_media:
            return False               # only start again what we paused
        self.paused_media = False
        if self.platform.startswith("linux"):
            return self.run(["playerctl", "play"])
        media = getattr(getattr(daemon, "ext", None), "media", None)
        try:
            return bool(media is not None and await media.control("toggle"))
        except Exception:
            return False

    # ------------------------------------------------------------ pointer
    @staticmethod
    def _gui() -> Any:
        try:
            import os_automation
            return os_automation.pyautogui if os_automation.PYAUTOGUI_AVAILABLE else None
        except Exception:
            return None

    def pointer(self, x: float, y: float, click: bool = False) -> bool:
        gui = self._gui()
        if gui is None:
            return False
        try:
            w, h = gui.size()
            px, py = int(x * (w - 1)), int(y * (h - 1))
            if click:
                gui.click(px, py)
            else:
                gui.moveTo(px, py)
            return True
        except Exception:
            return False


# ============================================================================================ the service
class Presence:
    def __init__(self, daemon: Any, data_dir: Optional[Path] = None, clock: Callable[[], float] = time.time,
                 actions: Optional[DesktopActions] = None,
                 camera_factory: Optional[Callable[[int], Any]] = None,
                 detector_factory: Optional[Callable[[], Any]] = None) -> None:
        self.d = daemon
        if data_dir is None:
            from config import settings
            data_dir = settings.data_dir
        self.data_dir = Path(data_dir)
        self.cfg_path = self.data_dir / "presence.json"
        self.time_path = self.data_dir / "presence_time.json"
        self.clock = clock
        self.actions = actions or DesktopActions()
        self.camera_factory = camera_factory or OpenCVCamera
        self.detector_factory = detector_factory or MediaPipeDetector
        self.cfg = self._load_cfg()
        self.on = False
        self.reason = ""                # why it's paused or off, in plain words
        self.error = ""
        self.loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self.detector: Any = None
        self.engine = G.GestureEngine(self.cfg["zone"])
        self.tracker = G.PresenceTracker(float(self.cfg["away_after_s"]))
        self.hand_seen = False
        self.last_hand: Optional[dict[str, Any]] = None
        self.last_gesture: Optional[dict[str, Any]] = None
        self.calibrating: Optional[list[tuple[float, float]]] = None
        self.current_card: tuple[str, float] = ("", 0.0)
        self._last_mark = -1e9
        self._time_saved_at = 0.0
        self.screen_today = self._load_time().get(self._today(), 0.0)
        self._base_screen = self.screen_today

    # ------------------------------------------------------------ settings (never frames)
    def _load_cfg(self) -> dict[str, Any]:
        cfg = dict(DEFAULTS)
        try:
            saved = json.loads(self.cfg_path.read_text(encoding="utf-8"))
            if isinstance(saved, dict):
                cfg.update({k: v for k, v in saved.items() if k in DEFAULTS})
        except (OSError, ValueError):
            pass
        return cfg

    def _save_cfg(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        tmp = self.cfg_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.cfg, indent=1), encoding="utf-8")
        os.replace(tmp, self.cfg_path)

    def configure(self, changes: dict[str, Any]) -> dict[str, Any]:
        bools = {k for k, v in DEFAULTS.items() if isinstance(v, bool)}
        for k, v in (changes or {}).items():
            if k not in DEFAULTS or k == "zone":
                continue
            if k in bools:
                self.cfg[k] = bool(v)
            elif k in ("away_after_s",):
                self.cfg[k] = int(min(600, max(10, float(v))))
            elif k in ("fps", "idle_fps"):
                self.cfg[k] = int(min(15, max(1, float(v))))
            elif k == "camera_index":
                self.cfg[k] = int(min(9, max(0, int(v))))
        if "zone" in (changes or {}):
            self.set_zone(changes["zone"])
        self.tracker.away_after_s = float(self.cfg["away_after_s"])
        self._save_cfg()
        return self.status()

    def set_zone(self, zone: Any) -> bool:
        try:
            x0, y0, x1, y1 = (min(1.0, max(0.0, float(v))) for v in zone)
        except (TypeError, ValueError):
            return False
        if x1 - x0 < 0.2 or y1 - y0 < 0.2:
            return False
        self.cfg["zone"] = [round(x0, 3), round(y0, 3), round(x1, 3), round(y1, 3)]
        self.engine.zone = tuple(self.cfg["zone"])
        return True

    # ------------------------------------------------------------ screen time (one number per day)
    @staticmethod
    def _today() -> str:
        return time.strftime("%Y-%m-%d")

    def _load_time(self) -> dict[str, float]:
        try:
            d = json.loads(self.time_path.read_text(encoding="utf-8"))
            return {str(k): float(v) for k, v in d.items()} if isinstance(d, dict) else {}
        except (OSError, ValueError, TypeError):
            return {}

    def _save_time(self) -> None:
        d = self._load_time()
        d[self._today()] = round(self.screen_today, 1)
        d = dict(sorted(d.items())[-14:])
        try:
            self.data_dir.mkdir(parents=True, exist_ok=True)
            tmp = self.time_path.with_suffix(".tmp")   # atomic: a crash mid-write can't blank the history
            tmp.write_text(json.dumps(d), encoding="utf-8")
            os.replace(tmp, self.time_path)
        except OSError:
            pass

    # ------------------------------------------------------------ status
    def status(self) -> dict[str, Any]:
        return {
            "on": self.on,
            "running": bool(self._thread and self._thread.is_alive()),
            "paused": self.reason,
            "error": self.error,
            "missing": missing(),
            "camera_switch": camera_switch() if sys.platform.startswith("linux") else None,
            "present": self.tracker.present,
            "looking_away": self.tracker.looking_away,
            "screen_time_today_min": round(self.screen_today / 60, 1),
            "last_gesture": self.last_gesture,
            "calibrating": self.calibrating is not None,
            "config": dict(self.cfg),
        }

    def calibration(self) -> dict[str, Any]:
        """What the calibration screen draws: the zone, and where the hand is (points only, never a picture)."""
        t = self.clock()
        return {"on": self.on, "zone": list(self.cfg["zone"]), "hand": self.last_hand,
                "armed": self.engine.armed(t), "calibrating": self.calibrating is not None,
                "samples": len(self.calibrating or [])}

    # ------------------------------------------------------------ on / off (silent)
    def set_on(self, on: bool, source: str = "you") -> dict[str, Any]:
        if on and not self.on:
            gone = missing() if self.camera_factory is OpenCVCamera else []
            if gone:
                self.error = "The camera add-on isn't installed: pip install " + " ".join(gone)
                return self.status()
            if camera_switch() == "off" and self.camera_factory is OpenCVCamera:
                self.error = "The camera is switched off in Settings > Privacy > Camera. Turn it on there first."
                return self.status()
            self.error, self.reason = "", ""
            self.on = True
            self.engine.reset()
            self.tracker = G.PresenceTracker(float(self.cfg["away_after_s"]))
            self._start_thread()
        elif not on and self.on:
            self.on = False
            self._stop.set()
            self.reason = ""
            self.last_hand = None
        self._signal()
        log.info("Presence camera %s (%s)", "on" if self.on else "off", source)
        return self.status()

    def toggle(self, source: str = "you") -> dict[str, Any]:
        return self.set_on(not self.on, source)

    def _signal(self) -> None:
        """Tell the core (black hole, phone) straight away; and the HUD's camera light."""
        try:
            import core_signals
            if core_signals._instance is not None:
                core_signals._instance.dirty = True
        except Exception:
            pass
        self._emit({"event": "PRESENCE_STATE", "on": self.on, "paused": self.reason})

    def _emit(self, event: dict[str, Any]) -> None:
        bus = getattr(self.d, "bus", None)
        if bus is None or self.loop is None:
            return
        try:
            asyncio.run_coroutine_threadsafe(bus.publish(event, record=False), self.loop)
        except Exception:
            pass

    # ------------------------------------------------------------ the camera loop (a thread)
    def _start_thread(self) -> None:
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, args=(self._stop,), name="presence", daemon=True)
        self._thread.start()

    def _budget_pause(self) -> str:
        """Why the camera should rest right now ('' = it shouldn't)."""
        if self.cfg["pause_in_fullscreen"]:
            try:
                import core_signals
                inst = core_signals._instance
                if inst is not None and inst.snapshot.get("paused"):
                    return "A full-screen app is in front, so the camera is resting."
            except Exception:
                pass
        return ""

    def _on_battery(self) -> bool:
        try:
            from core_signals import read_battery
            _pct, plugged = read_battery()
            return plugged is False
        except Exception:
            return False

    def _run(self, stop: threading.Event) -> None:
        cam = None
        last_battery = -1e9
        try:
            self.detector = self.detector_factory()
            while not stop.is_set():
                now = self.clock()
                if self.cfg["off_on_battery"] and now - last_battery >= BATTERY_EVERY_S:
                    last_battery = now
                    if self._on_battery():
                        self.on = False
                        self.reason = "Switched off on battery (you can change that in Settings)."
                        self._signal()
                        break
                why = self._budget_pause()
                if why:
                    if cam is not None:
                        cam.close()          # closed, so the camera light goes off too
                        cam = None
                    if why != self.reason:
                        self.reason = why
                        self._signal()
                    stop.wait(2.0)
                    continue
                if self.reason:
                    self.reason = ""
                    self._signal()
                if cam is None:
                    cam = self.camera_factory(int(self.cfg["camera_index"]))
                events = self.step(now, cam.read())
                if events and self.loop is not None:
                    asyncio.run_coroutine_threadsafe(self.dispatch(events), self.loop)
                fps = self.cfg["fps"] if self.hand_seen else self.cfg["idle_fps"]
                stop.wait(1.0 / max(1, int(fps)))
        except Exception as exc:
            self.error = str(exc)[:200]
            log.warning("Presence stopped: %s", exc)
            self.on = False
            self._signal()
        finally:
            if cam is not None:
                cam.close()
            if self.detector is not None and hasattr(self.detector, "close"):
                self.detector.close()
            self.detector = None
            self._save_time()

    def step(self, t: float, frame: Any) -> list[dict[str, Any]]:
        """One frame -> events. The frame is only handed to the detector and is not kept anywhere."""
        if frame is None:
            return []
        hands, face = self.detector(frame)
        del frame
        return self.handle(t, hands, face)

    def handle(self, t: float, hands: list[Any], face: Optional[dict[str, Any]]) -> list[dict[str, Any]]:
        """Landmarks -> events (pure numbers from here on)."""
        out: list[dict[str, Any]] = []
        hands = [h for h in (hands or []) if G.valid_hand(h)]
        self.hand_seen = bool(hands)
        if hands:
            h = max(hands, key=G.hand_size)
            c = G.palm_center(h)
            self.last_hand = {"x": round(c[0], 3), "y": round(c[1], 3), "pose": G.pose(h)}
            if self.calibrating is not None:
                self.calibrating.append(c)
                if len(self.calibrating) > 2000:
                    self.calibrating = self.calibrating[-2000:]
        else:
            self.last_hand = None
        before = self.tracker.screen_s
        for ev in self.tracker.feed(t, face is not None, G.facing(face)):
            out.append({"presence": ev})
        self.screen_today = self._base_screen + self.tracker.screen_s
        if self.tracker.screen_s > before and t - self._last_mark >= MARK_ACTIVITY_EVERY_S:
            self._last_mark = t
            out.append({"presence": "screen"})
        if self.cfg["gestures"] and self.calibrating is None:
            out += self.engine.feed(t, hands)
        if t - self._time_saved_at >= 60:
            self._time_saved_at = t
            self._save_time()
        return out

    # ------------------------------------------------------------ calibration
    def calibrate(self, action: str) -> dict[str, Any]:
        if action == "start":
            self.calibrating = []
        elif action == "reset":
            self.calibrating = None
            self.set_zone(G.DEFAULT_ZONE)
            self._save_cfg()
        elif action == "finish":
            pts, self.calibrating = self.calibrating or [], None
            if len(pts) >= 10:
                xs, ys = [p[0] for p in pts], [p[1] for p in pts]
                m = 0.08
                if self.set_zone([min(xs) - m, min(ys) - m, max(xs) + m, max(ys) + m]):
                    self._save_cfg()
                    return {**self.calibration(), "ok": True, "message": "Gesture zone saved."}
            return {**self.calibration(), "ok": False,
                    "message": "Move your hand around the area you want to use for a few seconds, then finish."}
        return self.calibration()

    # ------------------------------------------------------------ acting on events (on the daemon's loop)
    async def dispatch(self, events: list[dict[str, Any]]) -> list[dict[str, Any]]:
        results = []
        for ev in events:
            try:
                results.append(await self._act(ev))
            except Exception as exc:
                log.warning("Presence action %s failed: %s", ev, exc)
        return results

    async def _publish(self, event: dict[str, Any]) -> None:
        bus = getattr(self.d, "bus", None)
        if bus is not None:
            try:
                await bus.publish(event, record=False)
            except Exception:
                pass

    async def _act(self, ev: dict[str, Any]) -> dict[str, Any]:
        p = ev.get("presence")
        if p == "away":
            res = await asyncio.to_thread(self.actions.lock) if self.cfg["lock_when_away"] else "kept unlocked"
            await self._publish({"event": "PRESENCE", "kind": "away", "result": res})
            return {"away": res}
        if p == "back":
            await asyncio.to_thread(self.actions.wake)
            text = greeting()
            if self.cfg["greet"]:
                await self._publish({"event": "PRESENCE", "kind": "back", "text": text})
                if self.cfg["greet_aloud"] and hasattr(self.d, "speak"):
                    try:
                        await self.d.speak(text)
                    except Exception:
                        pass
            return {"back": text}
        if p == "look_away":
            return {"paused_media": await self.actions.pause_media(self.d) if self.cfg["pause_media"] else False}
        if p == "look_back":
            return {"resumed_media": await self.actions.resume_media(self.d)}
        if p == "screen":
            pacing = getattr(self.d, "pacing", None)
            if pacing is not None:
                pacing.mark_activity()        # real screen time keeps the focus arc going
            return {"screen": True}
        if p == "here":
            return {"here": True}
        g = ev.get("gesture")
        if not g:
            return {}
        if g not in ("pointer",):
            self.last_gesture = {"gesture": g, "at": self.clock(), **{k: v for k, v in ev.items() if k != "gesture"}}
        res: Any
        if g == "stop":
            res = await self.kill_switch()
        elif g == "thumbs_up":
            res = await self.approve_small_card()
        elif g in ("swipe_left", "swipe_right"):
            res = await asyncio.to_thread(self.actions.desktop, "next" if g == "swipe_left" else "prev")
        elif g == "rectangle":
            res = await asyncio.to_thread(self.actions.desktop, "new")
        elif g == "throw":
            res = await asyncio.to_thread(self.actions.throw, str(ev.get("direction")))
        elif g in ("pointer", "click"):
            res = await asyncio.to_thread(self.actions.pointer, float(ev["x"]), float(ev["y"]), g == "click") \
                if self.cfg["pointer"] else False
            if g == "pointer":
                return {"pointer": res}
        else:
            res = "hud"                      # spread / squeeze: the HUD zooms or opens/closes its panel
        await self._publish({"event": "PRESENCE_GESTURE", **ev, "result": res})
        return {g: res}

    async def kill_switch(self) -> Any:
        """Open palm: the same kill switch as the HUD button and 'Orion, freeze'. Unfreezing still needs the phone."""
        import freeze
        st = await freeze.freeze(self.d, by="open-palm gesture", why="You held up an open palm to the camera")
        return {"frozen": bool(st.get("frozen"))}

    def note_card(self, card_id: str) -> None:
        self.current_card = (str(card_id)[:64], self.clock())

    def _card(self) -> Optional[dict[str, Any]]:
        q = getattr(self.d, "approvals", None)
        if q is None:
            return None
        cid, at = self.current_card
        if cid and self.clock() - at <= CARD_MEMORY_S:
            return q.get(cid)
        try:
            pending = list(q.pending())
        except Exception:
            return None
        return pending[-1] if pending else None

    async def approve_small_card(self) -> dict[str, Any]:
        item = self._card()
        why = small_card_refusal(item)
        if why:
            return {"ok": False, "reason": why}
        await self.d.decide_approval(item["id"], "approve")  # type: ignore[index]
        return {"ok": True, "id": item["id"]}  # type: ignore[index]


# ============================================================================================ install + routes
_instance: Optional[Presence] = None


def camera_on() -> bool:
    """For the core signals (black hole, phone): is the camera on right now?"""
    return bool(_instance is not None and _instance.on and not _instance.reason)


def get(daemon: Any = None) -> Presence:
    global _instance
    if _instance is None:
        _instance = Presence(daemon)
    return _instance


def install(daemon: Any) -> Presence:
    """Called from the daemon's start(). The camera stays off unless you chose 'Start with Orion'."""
    p = get(daemon)
    p.d = daemon
    try:
        p.loop = asyncio.get_running_loop()
    except RuntimeError:
        p.loop = None
    if p.cfg.get("start_with_orion"):
        p.set_on(True, "start with Orion")
    return p


def uninstall() -> None:
    global _instance
    if _instance is not None:
        _instance.set_on(False, "Orion stopping")
        _instance = None


def make_router(get_daemon: Callable[[], Any]):
    router = APIRouter(prefix="/presence", tags=["presence"])

    async def body(request: Request) -> dict[str, Any]:
        try:
            b = await request.json()
        except ValueError:
            b = None
        return b if isinstance(b, dict) else {}

    def svc() -> Presence:
        p = get(get_daemon())
        if p.loop is None:
            try:
                p.loop = asyncio.get_running_loop()
            except RuntimeError:
                pass
        return p

    @router.get("")
    async def state() -> dict[str, Any]:
        return svc().status()

    @router.post("")
    async def switch(request: Request) -> dict[str, Any]:
        action = str((await body(request)).get("action") or "toggle")
        p = svc()
        if action == "on":
            return p.set_on(True)
        if action == "off":
            return p.set_on(False)
        if action == "toggle":
            return p.toggle()
        return {**p.status(), "error": "Use on, off or toggle."}

    @router.post("/config")
    async def config(request: Request) -> dict[str, Any]:
        return svc().configure(await body(request))

    @router.get("/calibration")
    async def calibration() -> dict[str, Any]:
        return svc().calibration()

    @router.post("/calibrate")
    async def calibrate(request: Request) -> dict[str, Any]:
        return svc().calibrate(str((await body(request)).get("action") or ""))

    @router.post("/current-card")
    async def current_card(request: Request) -> dict[str, Any]:
        svc().note_card(str((await body(request)).get("id") or ""))
        return {"ok": True}

    return router
