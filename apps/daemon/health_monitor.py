"""Health monitor (docs/ORIONOS_COMPLETE_PLAN.md Part 3 item 1).

Keeps checking that the screen, Orion's brain, voice, the phone link, the network, disk space, memory,
temperature and battery are fine, plus failed system services and the desktop (SwayFX). Each check says ok, warn
or fail, why in plain words, and a fix id the self-repair loop (Part 3 item 2) can act on.

Results go to:
  - GET /health/status (the HUD polls it with ?source=hud, which also counts as "the screen is alive")
  - a HEALTH_STATUS event on the bus when anything changes
  - the black hole: `turbulence` (0 all fine, more for warnings, most for failures), picked up by the HUD
  - your phone: alerts.emit("system", ...) once when a check newly fails (again after 12 hours if it stays failed)

Everything outside (sysfs, /proc, systemctl, the Ollama port) goes through small probe functions that tests swap.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import subprocess
import sys
import time
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

log = logging.getLogger("orion.health")

CHECK_EVERY_S = 60.0
REALERT_S = 12 * 3600
HUD_STALE_S = 90.0

# What each fix id means, in words (the self-repair loop will act on the ids).
FIXES = {
    "restart_hud": "Restart the Orion screen (Super+O, or orion-ctl hud).",
    "restart_daemon": "Restart Orion's brain (orion-ctl restart).",
    "setup_ai": "Set up a model: start Ollama, or add an online brain in Settings > Orion.",
    "pull_model": "Download the local model Orion is set to use (ollama pull).",
    "install_voice": "Install voice for Orion (orion-ctl setup voice).",
    "pair_phone": "Pair your phone (orion-ctl pair-phone).",
    "open_phone_app": "Open Orion on your phone so it reconnects.",
    "check_network": "Check Wi-Fi or the cable (Settings > Wi-Fi & network).",
    "free_space": "Free up space: empty the bin, remove big downloads or old apps.",
    "close_apps": "Close some apps or browser tabs.",
    "cool_down": "Let the computer cool down: check the vents aren't blocked.",
    "plug_in": "Plug in the charger.",
    "replace_battery": "The battery has worn down; consider replacing it.",
    "restart_unit": "Restart the service that stopped (see the details), or restart the computer.",
    "restart_desktop": "Log out and back in, or restart the computer.",
    "unfreeze": "Unfreeze Orion from your phone when you're ready.",
}
LABELS = {
    "hud": "Orion screen", "daemon": "Orion's brain", "ai": "AI model", "voice": "Voice", "phone": "Phone link",
    "network": "Network", "disk": "Disk space", "memory": "Memory", "temperature": "Temperature",
    "battery": "Battery", "services": "System services", "desktop": "Desktop (SwayFX)", "freeze": "Kill switch",
}
RANK = {"ok": 0, "warn": 1, "fail": 2}


def result(cid: str, status: str, reason: str, fix: str = "", detail: Any = None) -> dict[str, Any]:
    return {"id": cid, "label": LABELS.get(cid, cid), "status": status, "reason": reason, "fix": fix or None,
            "fix_words": FIXES.get(fix, "") if fix else "", "detail": detail}


def _read(path: Path) -> Optional[str]:
    try:
        return path.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return None


def _run(argv: list[str], timeout: float = 8) -> tuple[int, str]:
    try:
        r = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
        return r.returncode, r.stdout or ""
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 127, str(exc)


def _http_json(url: str, timeout: float = 2.0) -> Optional[Any]:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:  # noqa: S310 (localhost Ollama only)
            return json.loads(r.read().decode() or "null")
    except Exception:
        return None


@dataclass
class Probes:
    """Where the monitor looks. Tests point these at a fake sysfs / proc and fake commands."""
    sys_root: Path = Path("/sys")
    proc_root: Path = Path("/proc")
    run: Callable[[list[str], float], tuple[int, str]] = field(default=lambda a, t=8: _run(a, t))
    http_json: Callable[[str, float], Optional[Any]] = field(default=lambda u, t=2.0: _http_json(u, t))
    disk_usage: Callable[[str], Any] = shutil.disk_usage
    disk_paths: tuple[str, ...] = ("/", str(Path.home()))
    platform: str = sys.platform
    orionos: Optional[bool] = None       # None: look for /etc/orion
    now: Callable[[], float] = time.time
    import_ok: Callable[[str], bool] = field(default=lambda m: _importable(m))

    def is_orionos(self) -> bool:
        return Path("/etc/orion").is_dir() if self.orionos is None else self.orionos


def _importable(mod: str) -> bool:
    import importlib.util
    try:
        return importlib.util.find_spec(mod) is not None
    except (ImportError, ValueError):
        return False


# =====================================================================================  the checks
def check_hud(daemon: Any, p: Probes, last_hud: float) -> dict[str, Any]:
    clients = []
    try:
        clients = [c for c in daemon.bus.clients.values() if getattr(c, "kind", "") == "desktop"]
    except Exception:
        pass
    if clients or (last_hud and p.now() - last_hud < HUD_STALE_S):
        return result("hud", "ok", "The Orion screen is connected.")
    if not p.is_orionos():
        return result("hud", "ok", "The Orion screen isn't open (that's fine outside OrionOS).")
    if not last_hud:
        return result("hud", "warn", "The Orion screen hasn't connected since Orion started.", "restart_hud")
    return result("hud", "fail", f"The Orion screen stopped answering {int((p.now() - last_hud) // 60)} min ago.",
                  "restart_hud")


def check_daemon(daemon: Any, p: Probes) -> dict[str, Any]:
    stopped = []
    for t in list(getattr(daemon, "_tasks", []) or []):
        try:
            if t.done() and not t.cancelled():
                stopped.append(t.get_name())
        except Exception:
            continue
    if stopped:
        return result("daemon", "fail", "Part of Orion's brain stopped working: " + ", ".join(stopped) + ".",
                      "restart_daemon", stopped)
    return result("daemon", "ok", "All of Orion's background jobs are running.")


def check_ai(p: Probes, cfg: Any) -> dict[str, Any]:
    online = [n for n, k in (("Gemini", "gemini_api_key"), ("Claude", "anthropic_api_key"), ("OpenAI", "openai_api_key"))
              if getattr(cfg, k, "")]
    url = str(getattr(cfg, "ollama_url", "") or "http://127.0.0.1:11434").rstrip("/")
    model = str(getattr(cfg, "ollama_model", "") or "")
    tags = p.http_json(f"{url}/api/tags", 2.0)
    if isinstance(tags, dict):
        names = {str(m.get("name", "")) for m in tags.get("models") or [] if isinstance(m, dict)}
        if model and model not in names and f"{model}:latest" not in names:
            st = "ok" if online else "warn"
            return result("ai", st, f"Ollama runs, but the model {model} isn't downloaded"
                          + (f"; {', '.join(online)} is set up online." if online else "."), "pull_model")
        return result("ai", "ok", f"The local model ({model or 'Ollama'}) is ready"
                      + (f", and {', '.join(online)} online." if online else "."))
    if online:
        return result("ai", "ok" if not p.is_orionos() else "warn",
                      f"{', '.join(online)} is set up online, but the local model (Ollama) isn't answering.",
                      "setup_ai")
    return result("ai", "fail", "No AI model is answering: Ollama isn't running and no online brain is set up.",
                  "setup_ai")


def check_voice(daemon: Any, p: Probes) -> dict[str, Any]:
    stt = p.import_ok("faster_whisper")
    tts = p.import_ok("kokoro")
    if stt and tts:
        return result("voice", "ok", "Listening and speaking are installed.")
    missing = [w for w, ok in (("listening", stt), ("speaking", tts)) if not ok]
    st = "warn" if p.is_orionos() else "ok"
    return result("voice", st, f"Voice isn't fully installed ({' and '.join(missing)} missing).", "install_voice")


def check_phone(daemon: Any, p: Probes) -> dict[str, Any]:
    board = getattr(daemon, "board", None)
    phones_obj = getattr(board, "phones", None)
    if phones_obj is None:
        return result("phone", "ok", "The phone link isn't running here.")
    try:
        raw = phones_obj._phones()
    except Exception:
        raw = {}
    if not raw:
        return result("phone", "warn", "No phone is paired, so alerts and approvals can't reach you.", "pair_phone")
    last = 0.0
    for v in raw.values():
        t = float(v.get("last_ts") or 0)
        last = max(last, t / 1000 if t > 1e11 else t)
    if not last:
        return result("phone", "warn", "Your phone is paired but hasn't checked in yet.", "open_phone_app")
    age = p.now() - last
    if age > 3 * 86400:
        return result("phone", "warn", f"Your phone last checked in {int(age // 86400)} days ago.", "open_phone_app")
    words = "a moment ago" if age < 120 else f"{int(age // 60)} min ago" if age < 7200 else f"{int(age // 3600)} h ago"
    return result("phone", "ok", f"Your phone last checked in {words}.")


def check_network(p: Probes) -> dict[str, Any]:
    if p.platform != "linux":
        return result("network", "ok", "Not checked on this system.")
    up = []
    for d in sorted((p.sys_root / "class/net").glob("*")) if (p.sys_root / "class/net").is_dir() else []:
        if d.name == "lo":
            continue
        if _read(d / "operstate") == "up":
            up.append(d.name)
    route = _read(p.proc_root / "net/route") or ""
    has_default = any(len(l.split()) > 2 and l.split()[1] == "00000000" for l in route.splitlines()[1:])
    if up and has_default:
        return result("network", "ok", f"Connected ({', '.join(up[:3])}).")
    if up:
        return result("network", "warn", "Connected to a network, but there's no way out to the internet.",
                      "check_network")
    return result("network", "fail", "Not connected to any network.", "check_network")


def check_disk(p: Probes) -> dict[str, Any]:
    worst, worst_words, seen = "ok", [], set()
    live = _live_session(p)
    for path in p.disk_paths:
        try:
            u = p.disk_usage(path)
        except OSError:
            continue
        key = (u.total, u.free)
        if key in seen:
            continue
        seen.add(key)
        frac = u.free / u.total if u.total else 1
        gb = u.free / 1024 ** 3
        if live and path == "/":
            # On the USB stick / is a small (often in-memory) layer: only the share left matters, not the GB.
            st = "fail" if frac < 0.03 else "warn" if frac < 0.10 else "ok"
        else:
            st = "fail" if frac < 0.03 or gb < 1 else "warn" if frac < 0.10 or gb < 5 else "ok"
        if RANK[st] > RANK[worst]:
            worst = st
        worst_words.append(f"{gb:.1f} GB free on {path} ({frac * 100:.0f}%)")
    if not worst_words:
        return result("disk", "ok", "Not checked.")
    return result("disk", worst, ("Running out of space: " if worst != "ok" else "") + "; ".join(worst_words) + ".",
                  "free_space" if worst != "ok" else "")


def check_memory(p: Probes) -> dict[str, Any]:
    text = _read(p.proc_root / "meminfo")
    if not text:
        return result("memory", "ok", "Not checked on this system.")
    vals = {}
    for line in text.splitlines():
        k, _, v = line.partition(":")
        try:
            vals[k.strip()] = int(v.split()[0]) * 1024
        except (ValueError, IndexError):
            continue
    total, avail = vals.get("MemTotal", 0), vals.get("MemAvailable", 0)
    if not total:
        return result("memory", "ok", "Not checked.")
    frac = avail / total
    st = "fail" if frac < 0.05 else "warn" if frac < 0.12 else "ok"
    return result("memory", st, f"{avail / 1024 ** 3:.1f} GB of {total / 1024 ** 3:.1f} GB free ({frac * 100:.0f}%).",
                  "close_apps" if st != "ok" else "")


def check_temperature(p: Probes) -> dict[str, Any]:
    temps = []
    base = p.sys_root / "class/thermal"
    for z in sorted(base.glob("thermal_zone*")) if base.is_dir() else []:
        raw = _read(z / "temp")
        try:
            c = int(raw or "") / 1000
        except ValueError:
            continue
        if 0 < c < 150:
            temps.append((c, _read(z / "type") or z.name))
    if not temps:
        return result("temperature", "ok", "No temperature sensor found.")
    c, where = max(temps)
    st = "fail" if c >= 95 else "warn" if c >= 85 else "ok"
    return result("temperature", st, f"Hottest part: {c:.0f} °C ({where})." if st == "ok" else
                  f"Running hot: {c:.0f} °C ({where}).", "cool_down" if st != "ok" else "")


def check_battery(p: Probes) -> dict[str, Any]:
    base = p.sys_root / "class/power_supply"
    bats = [d for d in sorted(base.glob("*")) if _read(d / "type") == "Battery"] if base.is_dir() else []
    if not bats:
        return result("battery", "ok", "No battery (plugged-in computer).")
    b = bats[0]
    try:
        cap = int(_read(b / "capacity") or "")
    except ValueError:
        return result("battery", "ok", "Battery level unknown.")
    state = (_read(b / "status") or "").lower()
    discharging = state == "discharging"
    wear = None
    for full, design in (("energy_full", "energy_full_design"), ("charge_full", "charge_full_design")):
        try:
            wear = int(_read(b / full) or "") / int(_read(b / design) or "")
            break
        except (ValueError, ZeroDivisionError):
            continue
    if discharging and cap <= 7:
        return result("battery", "fail", f"Battery almost empty ({cap}%).", "plug_in")
    if discharging and cap <= 15:
        return result("battery", "warn", f"Battery low ({cap}%).", "plug_in")
    if wear is not None and wear < 0.6:
        return result("battery", "warn", f"Battery at {cap}%, but it only holds {wear * 100:.0f}% of what it did "
                      "when new.", "replace_battery")
    return result("battery", "ok", f"Battery {cap}% ({state or 'unknown'}).")


# Services that stop "with an error" by design on an OrionOS USB stick, so they never count as a problem.
HARMLESS_UNITS = {"casper-md5check.service"}


def _live_session(p: Probes) -> bool:
    """True when running from the USB stick (casper): / is then a small layer, often held in memory."""
    return "boot=casper" in (_read(p.proc_root / "cmdline") or "")


def _failed_units(p: Probes, user: bool) -> list[str]:
    argv = ["systemctl"] + (["--user"] if user else []) + ["--failed", "--no-legend", "--plain", "--no-pager"]
    rc, out = p.run(argv, 8)
    if rc not in (0, 1) and not out.strip():
        return []
    units = []
    for line in out.splitlines():
        parts = line.split()
        if parts and parts[0].endswith((".service", ".mount", ".socket", ".timer", ".target", ".path")) \
                and parts[0] not in HARMLESS_UNITS:
            units.append(parts[0])
    return units


def check_services(p: Probes) -> dict[str, Any]:
    if p.platform != "linux":
        return result("services", "ok", "Not checked on this system.")
    system, user = _failed_units(p, False), _failed_units(p, True)
    failed = system + [f"{u} (yours)" for u in user]
    if not failed:
        return result("services", "ok", "No services have stopped with an error.")
    orion = [u for u in failed if "orion" in u or "greetd" in u or "NetworkManager" in u]
    return result("services", "fail" if orion else "warn",
                  f"{len(failed)} service{'s' if len(failed) != 1 else ''} stopped with an error: "
                  + ", ".join(failed[:5]) + ("…" if len(failed) > 5 else "") + ".", "restart_unit", failed)


def check_desktop(p: Probes) -> dict[str, Any]:
    if p.platform != "linux":
        return result("desktop", "ok", "Not OrionOS (no desktop to check).")
    names = set()
    try:
        for d in p.proc_root.iterdir():
            if d.name.isdigit():
                n = _read(d / "comm")
                if n:
                    names.add(n)
    except OSError:
        pass
    if "sway" in names:
        return result("desktop", "ok", "The desktop (SwayFX) is running.")
    if not p.is_orionos():
        return result("desktop", "ok", "Not an OrionOS desktop session.")
    return result("desktop", "fail", "The desktop (SwayFX) isn't running.", "restart_desktop")


def check_freeze() -> dict[str, Any]:
    try:
        import freeze
        st = freeze.public()
    except Exception:
        return result("freeze", "ok", "The kill switch is off.")
    if st.get("frozen"):
        return result("freeze", "warn", "Orion is frozen: no self-changes, updates or agents.", "unfreeze")
    return result("freeze", "ok", "The kill switch is off.")


UPDATE_MARKERS = ("/run/reboot-required", "/var/run/reboot-required")


def core_feed(p: Probes, update_markers: tuple[str, ...] = UPDATE_MARKERS) -> dict[str, Any]:
    """CPU (load average per core), battery and "an update is ready" for the black hole (core_signals.py)."""
    out: dict[str, Any] = {}
    la = _read(p.proc_root / "loadavg")
    try:
        out["cpu"] = min(1.0, float((la or "").split()[0]) / max(1, os.cpu_count() or 1))
    except (ValueError, IndexError):
        pass
    base = p.sys_root / "class/power_supply"
    bats = [d for d in sorted(base.glob("*")) if _read(d / "type") == "Battery"] if base.is_dir() else []
    if bats:
        try:
            out["battery"] = float(int(_read(bats[0] / "capacity") or ""))
            out["plugged"] = (_read(bats[0] / "status") or "").lower() != "discharging"
        except ValueError:
            pass
    out["update_ready"] = any(Path(m).exists() for m in update_markers)
    return out


def send_to_core(values: dict[str, Any]) -> None:
    import core_signals
    for k, v in values.items():
        core_signals.set_external_signal(k, v)


def turbulence(checks: list[dict[str, Any]]) -> float:
    fails = sum(1 for c in checks if c["status"] == "fail")
    warns = sum(1 for c in checks if c["status"] == "warn")
    if fails:
        return min(1.0, 0.6 + 0.15 * (fails - 1) + 0.03 * warns)
    return min(0.45, 0.15 * warns)


def overall_words(checks: list[dict[str, Any]]) -> tuple[str, str]:
    fails = [c for c in checks if c["status"] == "fail"]
    warns = [c for c in checks if c["status"] == "warn"]
    if fails:
        return "fail", "Something needs attention: " + "; ".join(c["reason"] for c in fails[:3])
    if warns:
        return "warn", "Mostly fine. " + "; ".join(c["reason"] for c in warns[:3])
    return "ok", "Everything looks fine."


# =====================================================================================  the monitor
class HealthMonitor:
    def __init__(self, daemon: Any, probes: Optional[Probes] = None, cfg: Any = None,
                 every_s: float = CHECK_EVERY_S, feed: Optional[Callable[[dict[str, Any]], None]] = None) -> None:
        self.daemon = daemon
        # The black hole's cpu / battery / update_ready come from here (only for the real system by default).
        self.feed = feed if feed is not None else (send_to_core if probes is None else None)
        self.p = probes or Probes()
        self.cfg = cfg
        self.every_s = every_s
        self.last_hud = 0.0
        self.last: dict[str, Any] = {"status": "ok", "words": "Not checked yet.", "checks": [], "turbulence": 0.0,
                                     "at": 0}
        self._alerted: dict[str, float] = {}
        self._task: Optional[asyncio.Task] = None
        self._lock = asyncio.Lock()

    def hud_seen(self) -> None:
        self.last_hud = self.p.now()

    def _cfg(self) -> Any:
        if self.cfg is not None:
            return self.cfg
        from config import settings
        return settings

    def run_checks(self) -> list[dict[str, Any]]:
        d, p = self.daemon, self.p
        checks: list[tuple[str, Callable[[], dict[str, Any]]]] = [
            ("hud", lambda: check_hud(d, p, self.last_hud)),
            ("daemon", lambda: check_daemon(d, p)),
            ("ai", lambda: check_ai(p, self._cfg())),
            ("voice", lambda: check_voice(d, p)),
            ("phone", lambda: check_phone(d, p)),
            ("network", lambda: check_network(p)),
            ("disk", lambda: check_disk(p)),
            ("memory", lambda: check_memory(p)),
            ("temperature", lambda: check_temperature(p)),
            ("battery", lambda: check_battery(p)),
            ("services", lambda: check_services(p)),
            ("desktop", lambda: check_desktop(p)),
            ("freeze", check_freeze),
        ]
        out = []
        for cid, fn in checks:
            try:
                out.append(fn())
            except Exception as exc:
                log.exception("health check %s failed", cid)
                out.append(result(cid, "warn", f"This check couldn't run ({type(exc).__name__}).", ""))
        return out

    async def check_now(self) -> dict[str, Any]:
        async with self._lock:
            checks = await asyncio.to_thread(self.run_checks)
            status, words = overall_words(checks)
            snap = {"status": status, "words": words, "checks": checks, "turbulence": round(turbulence(checks), 3),
                    "at": self.p.now()}
            changed = [(c["id"], c["status"]) for c in checks] != [(c["id"], c["status"]) for c in self.last["checks"]]
            prev = {c["id"]: c["status"] for c in self.last["checks"]}
            self.last = snap
        self._alert_new_failures(checks, prev)
        if self.feed:
            try:
                self.feed(core_feed(self.p))
            except Exception:
                log.debug("core feed failed", exc_info=True)
        if changed:
            try:
                await self.daemon.bus.publish({"event": "HEALTH_STATUS", "health": snap})
            except Exception:
                pass
        return snap

    def _alert_new_failures(self, checks: list[dict[str, Any]], prev: dict[str, str]) -> None:
        now = self.p.now()
        for c in checks:
            if c["status"] != "fail":
                self._alerted.pop(c["id"], None)
                continue
            last = self._alerted.get(c["id"])
            if last is not None and now - last < REALERT_S:
                continue            # already told you; don't repeat every minute
            self._alerted[c["id"]] = now
            try:
                import alerts
                alerts.emit("system", f"{c['label']}: problem", c["reason"] + (f" {c['fix_words']}" if c.get(
                    "fix_words") else ""), ref=f"health:{c['id']}")
            except Exception:
                pass

    async def _loop(self) -> None:
        await asyncio.sleep(20)              # let everything start first
        while True:
            try:
                await self.check_now()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("health check round failed")
            await asyncio.sleep(self.every_s)

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.get_running_loop().create_task(self._loop(), name="health-monitor")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
            self._task = None


def make_router(get_monitor: Callable[[], HealthMonitor]):
    from fastapi import APIRouter

    router = APIRouter(prefix="/health", tags=["safety"])

    @router.get("/status")
    async def health_status(source: str = "", refresh: int = 0) -> Any:
        m = get_monitor()
        if source == "hud":
            m.hud_seen()
        if refresh or not m.last["checks"]:
            return await m.check_now()
        return m.last

    @router.post("/check")
    async def health_check() -> Any:
        return await get_monitor().check_now()

    return router
