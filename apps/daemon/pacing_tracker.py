"""Ambient Health, Conditioning & Pacing Tracker (V2 + Feature Bible D5).

Measures continuous desk time without any camera or extra sensor:
  * perceptual-hash screen deltas from capture_engine (something on screen changed), and
  * OS keyboard/mouse idle time (Windows GetLastInputInfo) so reading a static page still counts.

A session starts at the first activity and resets after `break_reset` minutes of no activity
(i.e. you actually stepped away). The live session drives the bottom arc of the Cosmic Core
(0 -> 90 min). At the limit the arc turns Warm Amber, the Celestial Chime plays and Orion
offers a casual voice check-in with one routine from DailyRoutines (calisthenics, yoga,
breathwork or hydration).

Feature Bible D5 additions:
  * 3-band stamina arc: Cyan 0-60 min ("focus"), Purple 60-90 ("warming"), Warm Amber 90+ ("break_due").
  * Pause-aware intervention: once due, Orion waits for a natural pause — you stop typing for a few
    seconds, switch windows, or commit — before chiming in (never mid-sentence), up to MAX_DEFER_S.
  * Conditioning roulette: routines come from conditioning_deck.json and rotate by category so the
    week covers calisthenics, mobility/yoga, breathwork and hydration evenly.
  * Hydration log and circadian wind-down (bedtime from the deck).
"""
from __future__ import annotations

import ctypes
import json
import sys
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional

ACTIVE_WINDOW_S = 60.0  # activity within this window counts as "at the desk"
# Screen changes alone (e.g. a video playing) only count while some input happened recently.
SCREEN_ONLY_MAX_IDLE_S = 300.0
PAUSE_IDLE_S = 12.0     # this long without keyboard/mouse input counts as a natural pause
MAX_DEFER_S = 20 * 60   # never hold a due check-in back longer than this

ROUTINES: list[dict[str, str]] = [
    {"id": "calisthenics", "kind": "calisthenics", "title": "Calisthenics burst",
     "detail": "10 squats, 10 push-ups, 20 seconds of plank.",
     "voice": "a quick calisthenics burst: ten squats, ten push-ups and a short plank"},
    {"id": "yoga", "kind": "yoga", "title": "Desk-release yoga",
     "detail": "Standing forward fold, neck rolls and a chest opener, 2 minutes.",
     "voice": "two minutes of yoga: a forward fold, some neck rolls and a chest opener"},
    {"id": "breathwork", "kind": "breathwork", "title": "Box breathing",
     "detail": "Inhale 4, hold 4, exhale 4, hold 4 for six rounds.",
     "voice": "six rounds of box breathing, four counts each way"},
    {"id": "hydration", "kind": "hydration", "title": "Hydration reset",
     "detail": "Refill and finish a full glass of water, eyes off the screen.",
     "voice": "a big glass of water and a minute with your eyes off the screen"},
]


def os_idle_seconds() -> Optional[float]:
    """Seconds since the last keyboard/mouse input (Windows, or Sway on OrionOS; None elsewhere)."""
    if sys.platform != "win32":
        import linux_desktop
        return linux_desktop.idle_seconds()
    try:
        class LASTINPUTINFO(ctypes.Structure):
            _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]

        info = LASTINPUTINFO()
        info.cbSize = ctypes.sizeof(LASTINPUTINFO)
        if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info)):
            return None
        get_tick = ctypes.windll.kernel32.GetTickCount
        get_tick.restype = ctypes.c_uint32  # default c_int goes negative after ~24.9 days of uptime
        millis = (get_tick() - info.dwTime) & 0xFFFFFFFF  # modular: survives the 49.7-day wrap
        return millis / 1000.0
    except Exception:
        return None


class PacingTracker:
    def __init__(self, limit_minutes: float = 90.0, break_reset_minutes: float = 5.0,
                 snooze_minutes: float = 15.0, warn_minutes: float = 60.0,
                 log_path: Optional[Path] = None) -> None:
        self.limit_s = max(60.0, limit_minutes * 60)
        self.warn_s = max(30.0, min(warn_minutes * 60, self.limit_s))
        self.reset_s = max(60.0, break_reset_minutes * 60)
        self.snooze_s = max(60.0, snooze_minutes * 60)
        self._lock = threading.Lock()
        self.session_start: Optional[float] = None
        self.last_activity: Optional[float] = None
        self.next_checkin_at: float = self.limit_s  # session-relative seconds
        self.checkins = 0
        self.breaks_taken = 0
        self.on_break_since: Optional[float] = None
        self.routines: list[dict[str, Any]] = [dict(r) for r in ROUTINES]
        self.pending_checkin: Optional[dict[str, Any]] = None
        self.armed_at: Optional[float] = None
        self.log_path = log_path

    # ----------------------------------------------------------- deck
    def configure(self, deck: dict[str, Any]) -> None:
        """Apply conditioning_deck.json (focus length, warn band, routines)."""
        with self._lock:
            try:
                limit = float(deck.get("focus_block_minutes") or self.limit_s / 60)
                self.limit_s = max(60.0, limit * 60)
                self.next_checkin_at = max(self.next_checkin_at, 0) if self.pending_checkin else self.limit_s
                warn = float(deck.get("warn_at_minutes") or limit * 2 / 3)
                self.warn_s = max(30.0, min(warn * 60, self.limit_s))
            except (TypeError, ValueError):
                pass
            routines = []
            for r in deck.get("routines", []) or []:
                if not isinstance(r, dict):
                    continue
                kind = str(r.get("category") or r.get("kind") or "calisthenics").lower()
                routines.append({
                    "id": str(r.get("id") or r.get("title") or kind),
                    "kind": kind,
                    "title": str(r.get("title") or str(r.get("id", kind)).replace("_", " ").title()),
                    "detail": str(r.get("detail") or ""),
                    "duration_min": float(r.get("duration_min") or 3),
                    "voice_prompt": str(r.get("voice_prompt") or ""),
                    "voice": str(r.get("voice") or r.get("detail") or r.get("title") or ""),
                })
            if routines:
                self.routines = routines

    def _week_log(self) -> list[dict[str, Any]]:
        if not self.log_path:
            return []
        try:
            data = json.loads(self.log_path.read_text(encoding="utf-8"))
            week_ago = time.time() - 7 * 86400
            return [e for e in data if e.get("at", 0) >= week_ago]
        except (OSError, ValueError):
            return []

    def record_break(self, routine_id_or_kind: Optional[str]) -> None:
        if not self.log_path:
            return
        routine = next((r for r in self.routines if r["id"] == routine_id_or_kind), None)
        kind = routine["kind"] if routine else (routine_id_or_kind or "break")
        entries = self._week_log() + [{"at": time.time(), "kind": kind, "id": routine["id"] if routine else kind}]
        try:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            self.log_path.write_text(json.dumps(entries[-500:]), encoding="utf-8")
        except OSError:
            pass

    def mark_last_skipped(self) -> None:
        """The last recorded break was skipped (Sanctuary 'skip this once') — kept so the roulette can adapt."""
        if not self.log_path:
            return
        entries = self._week_log()
        if entries:
            entries[-1]["skipped"] = True
            try:
                self.log_path.write_text(json.dumps(entries[-500:]), encoding="utf-8")
            except OSError:
                pass

    def pick_routine(self) -> dict[str, Any]:
        """Conditioning roulette: least-done category this week, then least-recently-used routine in it."""
        log_entries = self._week_log()
        counts: dict[str, int] = {}
        last_used: dict[str, float] = {}
        for e in log_entries:
            counts[e.get("kind", "")] = counts.get(e.get("kind", ""), 0) + 1
            last_used[e.get("id", "")] = max(last_used.get(e.get("id", ""), 0), e.get("at", 0))
        movement = [r for r in self.routines if r["kind"] != "hydration"] or self.routines
        kinds = sorted({r["kind"] for r in movement}, key=lambda k: (counts.get(k, 0), k))
        kind = kinds[0]
        # rotate deterministically when the log is empty so consecutive check-ins differ
        if not log_entries:
            kind = kinds[self.checkins % len(kinds)]
        pool = [r for r in movement if r["kind"] == kind]
        return min(pool, key=lambda r: last_used.get(r["id"], 0))

    # ----------------------------------------------------------- signals
    def mark_activity(self, ts: Optional[float] = None) -> None:
        """Called on every meaningful pHash delta / focus change."""
        ts = ts or time.time()
        with self._lock:
            self._touch(ts)

    def _touch(self, ts: float) -> None:
        if self.last_activity is not None and ts - self.last_activity > self.reset_s:
            self._reset_session()
        if self.session_start is None:
            self.session_start = ts
            self.on_break_since = None
        self.last_activity = ts

    def _reset_session(self) -> None:
        self.session_start = None
        self.next_checkin_at = self.limit_s
        self.pending_checkin = None
        self.armed_at = None

    # ----------------------------------------------------------- evaluate
    def evaluate(self, last_screen_change: Optional[float] = None, now: Optional[float] = None,
                 pause_signal: bool = False) -> Optional[dict[str, Any]]:
        """Run periodically. Returns a check-in payload once per due break, at a natural pause."""
        now = now or time.time()
        idle = os_idle_seconds()
        with self._lock:
            screen_recent = last_screen_change is not None and now - last_screen_change < ACTIVE_WINDOW_S
            if idle is None:  # no OS idle signal (non-Windows): fall back to screen deltas
                active = screen_recent
            else:
                active = idle < ACTIVE_WINDOW_S or (screen_recent and idle < SCREEN_ONLY_MAX_IDLE_S)
            if active and self.on_break_since is not None and now - self.on_break_since < 60:
                active = False  # grace period right after accepting a break
            if active:
                self._touch(now)
            elif self.last_activity is not None and now - self.last_activity > self.reset_s:
                if self.session_start is not None:
                    self.breaks_taken += 1
                self._reset_session()

            elapsed = self.elapsed(now)
            if self.session_start is None or elapsed < self.next_checkin_at or self.pending_checkin is not None:
                return None
            if self.armed_at is None:
                self.armed_at = now
            paused = idle is None or idle >= PAUSE_IDLE_S or pause_signal
            if not paused and now - self.armed_at < MAX_DEFER_S:
                return None  # wait for a natural pause instead of interrupting
            routine = self.pick_routine()
            self.checkins += 1
            self.armed_at = None
            self.pending_checkin = {
                "id": f"pace-{int(now)}",
                "minutes": round(elapsed / 60),
                "suggestion": routine,
                "options": self._options(routine),
            }
            return self.pending_checkin

    def _options(self, suggested: dict[str, Any]) -> list[dict[str, Any]]:
        """One routine per category (suggested first) so the card stays compact."""
        seen, out = {suggested["kind"]}, [suggested]
        for r in self.routines:
            if r["kind"] not in seen:
                seen.add(r["kind"])
                out.append(r)
        return out[:4]

    def elapsed(self, now: Optional[float] = None) -> float:
        if self.session_start is None:
            return 0.0
        return max(0.0, (now or time.time()) - self.session_start)

    # ----------------------------------------------------------- actions
    def snooze(self) -> None:
        with self._lock:
            self.next_checkin_at = self.elapsed() + self.snooze_s
            self.pending_checkin = None
            self.armed_at = None

    def start_break(self, routine: Optional[str] = None) -> None:
        with self._lock:
            self.breaks_taken += 1
            self.on_break_since = time.time()
            self.last_activity = None
            self._reset_session()
        self.record_break(routine)

    # ----------------------------------------------------------- state
    def snapshot(self) -> dict[str, Any]:
        now = time.time()
        with self._lock:
            elapsed = self.elapsed(now)
            if self.on_break_since is not None and self.session_start is None:
                phase = "resting"
            elif self.session_start is None:
                phase = "idle"
            elif self.pending_checkin is not None or elapsed >= self.limit_s:
                phase = "break_due"
            elif elapsed >= self.warn_s:
                phase = "warming"
            else:
                phase = "focus"
            return {
                "phase": phase,
                "session_minutes": round(elapsed / 60, 1),
                "limit_minutes": round(self.limit_s / 60),
                "warn_minutes": round(self.warn_s / 60),
                "snooze_minutes": round(self.snooze_s / 60),
                "progress": round(min(1.0, elapsed / self.limit_s), 4),
                "checkins": self.checkins,
                "breaks_taken": self.breaks_taken,
                "waiting_for_pause": self.armed_at is not None,
                "pending_checkin": self.pending_checkin,
            }


def checkin_voice_line(checkin: dict[str, Any]) -> str:
    suggestion = checkin.get("suggestion", {})
    if suggestion.get("voice_prompt"):
        return suggestion["voice_prompt"]
    minutes = checkin.get("minutes", 90)
    voice = suggestion.get("voice", "a short movement break")
    return (f"Hey, you've been locked in for {minutes} minutes straight. "
            f"Good moment for a reset? I'd suggest {voice}. Say the word and I'll hold your place.")


# ------------------------------------------------------------------ hydration & circadian
class Hydration:
    def __init__(self, path: Path, goal: int = 8) -> None:
        self.path = path
        self.goal = goal

    def _load(self) -> dict[str, int]:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def today(self) -> int:
        return int(self._load().get(datetime.now().strftime("%Y-%m-%d"), 0))

    def log_glass(self, n: int = 1) -> int:
        data = self._load()
        key = datetime.now().strftime("%Y-%m-%d")
        data[key] = max(0, int(data.get(key, 0)) + n)
        data = dict(sorted(data.items())[-60:])
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(data), encoding="utf-8")
        except OSError:
            pass
        return data[key]

    def snapshot(self) -> dict[str, Any]:
        return {"today": self.today(), "goal": self.goal}


def circadian_state(bedtime: str = "23:00", wind_down_minutes: int = 90, wake: str = "06:30",
                    now: Optional[datetime] = None, location: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    """normal | wind_down | night.

    With a saved location, wind-down starts at real sunset (Sun & Moon shift); otherwise at bedtime minus N minutes.
    """
    now = now or datetime.now()
    if location and location.get("lat") is not None and location.get("lon") is not None:
        from sun import sun_state

        base = circadian_state(bedtime, wind_down_minutes, wake, now)
        try:
            sun = sun_state(float(location["lat"]), float(location["lon"]), now.astimezone())
        except Exception:
            return base
        phase = base["phase"]
        if phase == "normal" and not sun["is_day"] and now.hour >= 12:
            phase = "wind_down"  # after sunset, before bedtime
        return {**base, "phase": phase, "sun": sun, "wind_down_starts": sun["sunset"] or base["wind_down_starts"],
                "trigger": "sunset"}


    def at(hhmm: str, base: datetime) -> datetime:
        try:
            h, m = (int(x) for x in hhmm.split(":"))
        except ValueError:
            h, m = 23, 0
        return base.replace(hour=h % 24, minute=m % 60, second=0, microsecond=0)

    bed = at(bedtime, now)
    wake_t = at(wake, now)
    # Bedtime after midnight (e.g. "00:30") belongs to the next day if we're past the wake time.
    if bed < wake_t and now > wake_t:
        bed += timedelta(days=1)
    start = bed - timedelta(minutes=wind_down_minutes)
    if start <= now < bed:
        phase = "wind_down"
    elif now >= bed or now < wake_t:
        phase = "night"
    else:
        phase = "normal"
    return {"phase": phase, "bedtime": bedtime, "wind_down_starts": start.strftime("%H:%M")}
