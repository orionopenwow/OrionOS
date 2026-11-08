"""Smart reactions for the black hole core (Art Update §5, build step 6). Calm by design.

The daemon turns what is happening into a few gentle numbers and small events, and the HUD's reaction reducer
(apps/desktop/src/lib/blackhole/reactions.ts) eases them into shader inputs:

  CORE_SIGNALS  (every few seconds, only when something changed, plus a slow heartbeat)
      thinking      0..1  models working: orchestrator calls in flight + coding agents working
      heat          0..0.5  compute spend in the last hour (capped, so an expensive hour is only a bit warmer)
      turbulence    0..0.7  CPU load (nothing below 40 %)
      dim           0..0.6  low battery while unplugged (smaller and dimmer)
      update_ready  0 | 1  an update is ready (soft second ring)
      quiet         0 | 1  inside learned quiet hours (dimmer and slower)
      paused        bool   a full-screen app is in front: the HUD stops drawing
      orbit         up to 7 pending approvals [{id, tone}], orbit_total = how many are waiting in all
      camera        bool   the presence webcam is on (presence.py, Phase 5): the core and the phone show it

  CORE_EVENT
      {"kind": "comet", "size": "ember"|"normal"|"tiny", "count": n}   a message or urgent alert falls in
      {"kind": "orbit", "id": ..., "outcome": "approve"|"reject"}       an approval was decided
      {"kind": "feed"}                                                  a dropped file reached the vault

Comets are rate limited (one every COMET_GAP_S, at most COMET_BURST per minute); extra ones fold into the next
comet's count, and digest ones are held and fall together as one small batch. A flood never spams the core.

Inputs are read without touching the modules that produce them: the bus is tapped for COMMS_MESSAGE,
APPROVAL_RESOLVED, BRIEFING and STAR events, the orchestrator's calls are counted, and alert posts are watched.
CPU and battery come straight from /proc and /sys (psutil if it is installed, for other systems). Anything else,
such as the health monitor, can push a value with `set_external_signal(name, value)`.

Routes: GET /core/signals (the current snapshot), POST /core/signal {"name", "value"} (external signals).
"""
from __future__ import annotations

import asyncio
import functools
import glob
import json
import logging
import math
import os
import shutil
import subprocess
import sys
import threading
import time
from collections import deque
from typing import Any, Callable, Optional

from fastapi import APIRouter
from pydantic import BaseModel, Field

log = logging.getLogger("orion.core")

TICK_S = 0.5            # comet drain and change check
SNAPSHOT_EVERY_S = 3.0  # how often the slow signals are recomputed
HEARTBEAT_S = 30.0      # publish the snapshot at least this often
QUIET_EVERY_S = 60.0
FULLSCREEN_EVERY_S = 4.0

ORBIT_MAX = 7
COMET_GAP_S = 2.5
COMET_BURST = 4          # at most this many comets per COMET_WINDOW_S
COMET_WINDOW_S = 60.0
DIGEST_HOLD_S = 45.0     # digest comets wait and fall together
COUNT_MAX = 9

HEAT_CAP = 0.5
HEAT_FULL_DOLLARS = 1.0  # spend per hour that reaches the cap
EXTERNAL_TTL_S = 120.0   # an external value older than this is ignored (except update_ready)

EXTERNAL_NAMES = ("cpu", "battery", "plugged", "update_ready", "fullscreen", "thinking", "heat")


# ---------------------------------------------------------------------------------------------- shaping (pure)
def _clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    try:
        x = float(x)
    except (TypeError, ValueError):
        return lo
    if math.isnan(x):
        return lo
    return max(lo, min(hi, x))


def _smoothstep(e0: float, e1: float, x: float) -> float:
    t = _clamp((x - e0) / (e1 - e0))
    return t * t * (3 - 2 * t)


def thinking_level(inflight: int, agents_working: int) -> float:
    """Models working -> 0..1. One call reads clearly (~0.55), more only adds a little."""
    x = max(0, int(inflight)) + 0.35 * max(0, int(agents_working))
    return round(_clamp(1 - math.exp(-x / 1.25)), 3)


def heat_level(spend_last_hour: float) -> float:
    """Dollars spent on models in the last hour -> subtle heat, capped at HEAT_CAP."""
    s = max(0.0, float(spend_last_hour or 0.0))
    return round(HEAT_CAP * math.sqrt(_clamp(s / HEAT_FULL_DOLLARS)), 3)


def turbulence_level(cpu: Optional[float]) -> float:
    """CPU load 0..1 -> turbulence. A normal working load changes nothing."""
    if cpu is None:
        return 0.0
    return round(0.7 * _smoothstep(0.4, 1.0, _clamp(cpu)), 3)


def battery_dim(percent: Optional[float], plugged: Optional[bool]) -> float:
    """Low battery while unplugged -> smaller and dimmer (0 at 25 %, 0.6 at 5 % and below)."""
    if percent is None or plugged:
        return 0.0
    return round(0.6 * _clamp((25.0 - float(percent)) / 20.0), 3)


class SpendWindow:
    """Spend in the last hour from samples of a running total (the compute meter's session cost)."""

    def __init__(self, window_s: float = 3600.0) -> None:
        self.window_s = window_s
        self.samples: deque[tuple[float, float]] = deque()

    def add(self, now: float, total: float) -> None:
        total = max(0.0, float(total or 0.0))
        if self.samples and total < self.samples[-1][1]:  # the meter restarted
            self.samples.clear()
        self.samples.append((now, total))
        # samples come every few seconds, so the oldest one inside the window is a close enough baseline
        while len(self.samples) > 1 and self.samples[0][0] < now - self.window_s:
            self.samples.popleft()

    def last_hour(self) -> float:
        if len(self.samples) < 2:
            return 0.0
        return max(0.0, self.samples[-1][1] - self.samples[0][1])


def comet_size(*, vip: bool = False, muted: bool = False, level: str = "normal") -> str:
    if vip or level == "urgent":
        return "ember"
    if muted or level == "digest":
        return "tiny"
    return "normal"


class CometQueue:
    """Batches and limits comets so a flood of messages never spams the core. Thread safe, clock injected."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.waiting: dict[str, int] = {"ember": 0, "normal": 0, "tiny": 0}
        self.digest_since: Optional[float] = None
        self.sent: deque[float] = deque()
        self.last_sent = -1e9

    def push(self, size: str, now: float) -> None:
        if size not in self.waiting:
            size = "normal"
        with self.lock:
            self.waiting[size] += 1
            if size == "tiny" and self.digest_since is None:
                self.digest_since = now

    def pending(self) -> int:
        with self.lock:
            return sum(self.waiting.values())

    def drain(self, now: float) -> list[dict[str, Any]]:
        """At most one comet per call. Ember first, then normal; digest ones wait DIGEST_HOLD_S and fall as one."""
        with self.lock:
            while self.sent and self.sent[0] <= now - COMET_WINDOW_S:
                self.sent.popleft()
            if now - self.last_sent < COMET_GAP_S:
                return []
            burst_full = len(self.sent) >= COMET_BURST
            for size in ("ember", "normal"):
                n = self.waiting[size]
                if n and (not burst_full or size == "ember" and now - self.last_sent >= COMET_GAP_S * 4):
                    # one comet carries everything of this size that piled up
                    self.waiting[size] = 0
                    return [self._send(size, n, now)]
            n = self.waiting["tiny"]
            if n and not burst_full and self.digest_since is not None and now - self.digest_since >= DIGEST_HOLD_S:
                self.waiting["tiny"] = 0
                self.digest_since = None
                return [self._send("tiny", n, now)]
            return []

    def _send(self, size: str, n: int, now: float) -> dict[str, Any]:
        self.sent.append(now)
        self.last_sent = now
        return {"kind": "comet", "size": size, "count": min(COUNT_MAX, n)}


_APPROVED = {"sending", "sent", "approved", "edited", "applied", "done", "executed"}
_REJECTED = {"rejected", "cancelled", "dismissed", "reverted", "expired"}


def resolution(item: dict[str, Any]) -> Optional[str]:
    """APPROVAL_RESOLVED item -> 'approve' | 'reject' | None (still pending, or a failed send going back)."""
    status = str(item.get("status") or "").lower()
    if status in _APPROVED:
        return "approve"
    if status in _REJECTED:
        return "reject"
    return None


def orbit_of(pending: list[dict[str, Any]]) -> tuple[list[dict[str, str]], int]:
    """Pending approvals -> up to ORBIT_MAX stars (oldest first, so a star keeps its place) and the total."""
    stars = []
    for item in pending[:ORBIT_MAX]:
        tone = "boardroom" if item.get("kind") == "boardroom" else "approval"
        stars.append({"id": str(item.get("id", ""))[:64], "tone": tone})
    return stars, len(pending)


# ---------------------------------------------------------------------------------------------- system readers
class CpuReader:
    """Whole-machine CPU load 0..1 from /proc/stat deltas (psutil elsewhere). None when unknown."""

    def __init__(self, path: str = "/proc/stat") -> None:
        self.path = path
        self.prev: Optional[tuple[int, int]] = None

    def read(self) -> Optional[float]:
        try:
            with open(self.path, encoding="ascii") as f:
                parts = f.readline().split()
            vals = [int(x) for x in parts[1:9]]
            idle = vals[3] + vals[4]
            total = sum(vals)
        except (OSError, ValueError, IndexError):
            try:
                import psutil  # type: ignore
                return _clamp(psutil.cpu_percent(interval=None) / 100.0)
            except Exception:
                return None
        prev, self.prev = self.prev, (idle, total)
        if prev is None or total <= prev[1]:
            return None
        return _clamp(1 - (idle - prev[0]) / (total - prev[1]))


def read_battery(root: str = "/sys/class/power_supply") -> tuple[Optional[float], Optional[bool]]:
    """(percent, plugged) or (None, None) on a desktop without a battery."""
    for supply in sorted(glob.glob(os.path.join(root, "*"))):
        try:
            with open(os.path.join(supply, "type"), encoding="ascii") as f:
                if f.read().strip() != "Battery":
                    continue
            with open(os.path.join(supply, "capacity"), encoding="ascii") as f:
                pct = float(f.read().strip())
            try:
                with open(os.path.join(supply, "status"), encoding="ascii") as f:
                    status = f.read().strip().lower()
            except OSError:
                status = ""
            return pct, status != "discharging"
        except (OSError, ValueError):
            continue
    if not sys.platform.startswith("linux"):
        try:
            import psutil  # type: ignore
            b = psutil.sensors_battery()
            if b is not None:
                return float(b.percent), bool(b.power_plugged)
        except Exception:
            pass
    return None, None


def _focused(node: dict[str, Any]) -> Optional[dict[str, Any]]:
    if node.get("focused"):
        return node
    for child in (node.get("nodes") or []) + (node.get("floating_nodes") or []):
        hit = _focused(child)
        if hit is not None:
            return hit
    return None


def fullscreen_from_tree(tree: dict[str, Any]) -> bool:
    """Sway tree -> is a full-screen window other than Orion in front?"""
    node = _focused(tree)
    if not node or not node.get("fullscreen_mode"):
        return False
    who = f"{node.get('app_id') or ''} {(node.get('window_properties') or {}).get('class') or ''} {node.get('name') or ''}"
    return "orion" not in who.lower()


def read_fullscreen() -> Optional[bool]:
    if not os.environ.get("SWAYSOCK") or not shutil.which("swaymsg"):
        return None
    try:
        out = subprocess.run(["swaymsg", "-t", "get_tree", "-r"], capture_output=True, text=True, timeout=1.5)
        return fullscreen_from_tree(json.loads(out.stdout)) if out.returncode == 0 else None
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


# ---------------------------------------------------------------------------------------------- external signals
_external: dict[str, tuple[float, float]] = {}
_external_lock = threading.Lock()


def set_external_signal(name: str, value: Any, now: Optional[float] = None) -> bool:
    """For other modules (the health monitor): set cpu 0..1, battery 0..100, plugged, update_ready, fullscreen,
    thinking or heat. Returns False for an unknown name. Values expire after EXTERNAL_TTL_S (update_ready doesn't)."""
    if name not in EXTERNAL_NAMES:
        return False
    if isinstance(value, bool):
        value = 1.0 if value else 0.0
    hi = 100.0 if name == "battery" else 1.0
    with _external_lock:
        _external[name] = (_clamp(value, 0.0, hi), time.time() if now is None else now)
    if _instance is not None:
        _instance.dirty = True
    return True


def external(name: str, now: Optional[float] = None) -> Optional[float]:
    with _external_lock:
        hit = _external.get(name)
    if hit is None:
        return None
    value, at = hit
    if name != "update_ready" and (time.time() if now is None else now) - at > EXTERNAL_TTL_S:
        return None
    return value


def _camera_on() -> bool:
    """Phase 5: is the presence camera on? Read from presence.py only if it is loaded (never imports it)."""
    mod = sys.modules.get("presence")
    try:
        return bool(mod is not None and mod.camera_on())
    except Exception:
        return False


# ---------------------------------------------------------------------------------------------- the live service
class CoreSignals:
    def __init__(self, daemon: Any, clock: Callable[[], float] = time.time) -> None:
        self.d = daemon
        self.clock = clock
        self.comets = CometQueue()
        self.spend = SpendWindow()
        self.cpu = CpuReader()
        self.inflight = 0
        self.dirty = True
        self.snapshot: dict[str, Any] = {}
        self._last_published: Optional[dict[str, Any]] = None
        self._last_pub_at = 0.0
        self._last_snap_at = -1e9
        self._quiet = (0.0, -1e9)      # value, read at
        self._fullscreen = (False, -1e9)
        self._resolved: deque[str] = deque(maxlen=64)
        self._events: deque[dict[str, Any]] = deque(maxlen=32)
        self._task: Optional[asyncio.Task] = None
        self._undo: list[Callable[[], None]] = []

    # ------------------------------------------------------------ taps (non-invasive; undone on stop)
    def attach(self) -> None:
        bus = getattr(self.d, "bus", None)
        if bus is not None:
            orig_publish = bus.publish

            @functools.wraps(orig_publish)
            async def publish(event: dict[str, Any], *args: Any, **kwargs: Any) -> Any:
                try:
                    self.observe(event)
                except Exception:
                    log.debug("core tap failed", exc_info=True)
                return await orig_publish(event, *args, **kwargs)

            bus.publish = publish
            self._undo.append(lambda: setattr(bus, "publish", orig_publish))

        orch = getattr(self.d, "orchestrator", None)
        for name in ("handle", "frontier_text"):
            fn = getattr(orch, name, None) if orch is not None else None
            if fn is None or not asyncio.iscoroutinefunction(fn):
                continue
            setattr(orch, name, self._counted(fn))
            self._undo.append(functools.partial(setattr, orch, name, fn))

        alerts = getattr(self.d, "alerts", None)
        post = getattr(alerts, "post", None) if alerts is not None else None
        if post is not None:
            @functools.wraps(post)
            def watched(*args: Any, **kwargs: Any) -> Any:
                a = post(*args, **kwargs)
                try:
                    self.observe_alert(a)
                except Exception:
                    log.debug("core alert tap failed", exc_info=True)
                return a

            alerts.post = watched
            self._undo.append(lambda: setattr(alerts, "post", post))

    def detach(self) -> None:
        while self._undo:
            try:
                self._undo.pop()()
            except Exception:
                pass

    def _counted(self, fn: Callable[..., Any]) -> Callable[..., Any]:
        @functools.wraps(fn)
        async def run(*args: Any, **kwargs: Any) -> Any:
            self.inflight += 1
            self.dirty = True
            try:
                return await fn(*args, **kwargs)
            finally:
                self.inflight = max(0, self.inflight - 1)
                self.dirty = True
        return run

    # ------------------------------------------------------------ observing
    def observe(self, event: dict[str, Any]) -> None:
        kind = event.get("event")
        now = self.clock()
        if kind == "COMMS_MESSAGE":
            msg = event.get("message") or {}
            if msg.get("direction") != "in" or msg.get("backfill"):
                return
            thread = event.get("thread") or {}
            self.comets.push(comet_size(vip=bool(msg.get("vip") or thread.get("vip")), muted=bool(thread.get("muted")),
                                        level=self._message_level()), now)
        elif kind == "APPROVAL_REQUIRED":
            self.dirty = True
        elif kind == "APPROVAL_RESOLVED":
            item = event.get("item") or {}
            outcome = resolution(item)
            iid = str(item.get("id", ""))
            self.dirty = True
            if outcome and iid and iid not in self._resolved:
                self._resolved.append(iid)
                self._events.append({"kind": "orbit", "id": iid[:64], "outcome": outcome})
        elif kind == "BRIEFING":  # extensions.vault_drop: the dropped file is in the vault
            self._events.append({"kind": "feed"})
        elif kind == "STAR":      # a long terminal command finished while you were elsewhere
            self.comets.push("normal" if event.get("ok", True) else "ember", now)

    def observe_alert(self, a: Any) -> None:
        if not isinstance(a, dict) or a.get("type") in ("approval", "messages"):
            return  # approvals orbit; messages already arrive through the comms stream
        if a.get("level") == "urgent":
            self.comets.push("ember", self.clock())

    def _message_level(self) -> str:
        alerts = getattr(self.d, "alerts", None)
        try:
            return str(alerts.effective_level("messages")) if alerts is not None else "normal"
        except Exception:
            return "normal"

    # ------------------------------------------------------------ the snapshot
    def _agents_working(self) -> int:
        tk = getattr(self.d, "agent_toolkit", None)
        try:
            agents = tk.agents() if tk is not None else []
        except Exception:
            agents = []
        return sum(1 for a in agents if isinstance(a, dict) and a.get("state") == "working")

    def _session_cost(self) -> float:
        try:
            return float(self.d.orchestrator.meter.brief()["session_cost"])
        except Exception:
            return 0.0

    def _pending(self) -> list[dict[str, Any]]:
        try:
            return list(self.d.approvals.pending())
        except Exception:
            return []

    def _read_quiet(self, now: float) -> float:
        value, at = self._quiet
        if now - at >= QUIET_EVERY_S:
            try:
                hours = self.d.alerts.quiet_hours()
                value = 1.0 if time.localtime(now).tm_hour in hours else 0.0
            except Exception:
                value = 0.0
            self._quiet = (value, now)
        return value

    def compute(self, now: Optional[float] = None, *, cpu: Optional[float] = None,
                battery: tuple[Optional[float], Optional[bool]] = (None, None),
                fullscreen: Optional[bool] = None) -> dict[str, Any]:
        """Build the CORE_SIGNALS snapshot from the daemon's state plus the given readings (externals win)."""
        now = self.clock() if now is None else now
        self.spend.add(now, self._session_cost())
        ext_cpu = external("cpu", now)
        ext_bat, ext_plug = external("battery", now), external("plugged", now)
        pct = ext_bat if ext_bat is not None else battery[0]
        plugged = (ext_plug >= 0.5) if ext_plug is not None else battery[1]
        ext_full = external("fullscreen", now)
        full = (ext_full >= 0.5) if ext_full is not None else bool(fullscreen)
        thinking = thinking_level(self.inflight, self._agents_working())
        ext_think = external("thinking", now)
        if ext_think is not None:
            thinking = max(thinking, round(ext_think, 3))
        heat = heat_level(self.spend.last_hour())
        ext_heat = external("heat", now)
        if ext_heat is not None:
            heat = max(heat, round(min(HEAT_CAP, ext_heat), 3))
        stars, total = orbit_of(self._pending())
        resolved = set(self._resolved)
        stars = [s for s in stars if s["id"] not in resolved]
        cpu_now = ext_cpu if ext_cpu is not None else cpu
        return {
            "thinking": thinking,
            "heat": heat,
            "turbulence": turbulence_level(cpu_now),
            "dim": battery_dim(pct, plugged),
            "update_ready": 1.0 if (external("update_ready", now) or 0) >= 0.5 else 0.0,
            "quiet": self._read_quiet(now),
            "paused": full,
            "orbit": stars,
            "orbit_total": total,
            "cpu": None if cpu_now is None else round(cpu_now, 2),
            "battery": None if pct is None else round(pct),
            "camera": _camera_on(),  # Phase 5: the webcam presence light (presence.py)
        }

    @staticmethod
    def _changed(a: Optional[dict[str, Any]], b: dict[str, Any]) -> bool:
        if a is None:
            return True
        for k, v in b.items():
            if k in ("cpu", "battery"):
                continue
            w = a.get(k)
            if isinstance(v, float) and isinstance(w, (int, float)) and not isinstance(w, bool):
                if abs(v - w) >= 0.04 or (v == 0) != (w == 0):
                    return True
            elif v != w:
                return True
        return False

    async def step(self, now: Optional[float] = None) -> list[dict[str, Any]]:
        """One loop pass: maybe recompute and publish the snapshot, then events and at most one comet."""
        now = self.clock() if now is None else now
        out: list[dict[str, Any]] = []
        if self.dirty or now - self._last_snap_at >= SNAPSHOT_EVERY_S:
            self.dirty = False
            self._last_snap_at = now
            cpu = await asyncio.to_thread(self.cpu.read)
            battery = await asyncio.to_thread(read_battery)
            if now - self._fullscreen[1] >= FULLSCREEN_EVERY_S:
                self._fullscreen = (bool(await asyncio.to_thread(read_fullscreen)), now)
            self.snapshot = self.compute(now, cpu=cpu, battery=battery, fullscreen=self._fullscreen[0])
            if self._changed(self._last_published, self.snapshot) or now - self._last_pub_at >= HEARTBEAT_S:
                self._last_published = dict(self.snapshot)
                self._last_pub_at = now
                out.append({"event": "CORE_SIGNALS", **self.snapshot})
        while self._events:
            out.append({"event": "CORE_EVENT", **self._events.popleft()})
        for c in self.comets.drain(now):
            out.append({"event": "CORE_EVENT", **c})
        bus = getattr(self.d, "bus", None)
        for ev in out:
            if bus is not None:
                await bus.publish(ev, record=False)
        return out

    # ------------------------------------------------------------ lifecycle
    def start(self) -> None:
        self.attach()

        async def loop() -> None:
            while True:
                try:
                    await self.step()
                except asyncio.CancelledError:
                    raise
                except Exception:
                    log.exception("core signals step failed")
                await asyncio.sleep(TICK_S)

        if self._task is None:
            self._task = asyncio.create_task(loop(), name="core-signals")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            self._task = None
        self.detach()


_instance: Optional[CoreSignals] = None


def install(daemon: Any) -> CoreSignals:
    """Called once from the daemon's start()."""
    global _instance
    if _instance is not None:
        _instance.detach()
    _instance = CoreSignals(daemon)
    _instance.start()
    return _instance


async def uninstall() -> None:
    global _instance
    if _instance is not None:
        await _instance.stop()
        _instance = None


class ExternalSignal(BaseModel):
    name: str = Field(..., max_length=32)
    value: float = Field(..., ge=0, le=100)


def make_router(get_daemon: Callable[[], Any]) -> APIRouter:
    router = APIRouter()

    @router.get("/core/signals")
    async def core_signals() -> dict[str, Any]:
        return dict(_instance.snapshot) if _instance is not None else {}

    @router.post("/core/signal")
    async def core_signal(s: ExternalSignal) -> dict[str, Any]:
        ok = set_external_signal(s.name, s.value)
        return {"ok": ok} if ok else {"ok": False, "error": f"unknown signal; use one of {', '.join(EXTERNAL_NAMES)}"}

    return router
