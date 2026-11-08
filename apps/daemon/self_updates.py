"""Everything stays current (docs/ORIONOS_COMPLETE_PLAN.md Part 3 item 5).

One small scheduler (hourly tick, each job on its own rhythm) that keeps each kind of software current, every
kind through the permission levels (self_levels.decide) and the same pattern: an undo point first, a check after,
undo on failure, a line in the change history.

  System packages, security   unattended-upgrades does the work (Ubuntu's own timer). OrionOS wraps it:
  (and the browser: Firefox    - a snapshot before apt changes anything (the apt hook in os/safety),
   comes from apt)             - a gate (orion-self-gate) so it only runs by itself at "tell me after" or "just do
                                 it", and never while frozen,
                               - after it ran, orion-selftest after-update calls POST /self/after-update: Orion
                                 checks its health again, writes the change, and if things got worse tells you
                                 and offers to go back to the snapshot (that asks for the admin password).
                               At "always ask" Orion looks daily and shows a card when security updates wait.
  Flatpak apps                 Get apps' weekly run (os/getapps) asks GET /self/decide/app_update first; it already
                               snapshots, checks each app starts and puts back a broken one.
  AI models (Ollama)           weekly: a tiny question to the model first, a copy of the current model kept,
                               `pull` the configured tag, the tiny question again; a broken model is put back.
  Firmware (fwupd)             weekly: refresh and list what's waiting as a card. Never flashed by Orion.
  New system versions          daily: update_channels.check_and_offer (tested in a VM first, always asks).

HTTP (local):  GET /self/updates   POST /self/updates/run {job}   POST /self/updates/settings {vm_test}
               POST /self/after-update {kind}   (from the root hook)
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import threading
from pathlib import Path
from typing import Any, Callable, Optional

import self_cards
import self_host
import self_levels

log = logging.getLogger("orion.self_updates")

TICK_S = 3600.0
DAY = 86400.0
JOBS = {"models": 7 * DAY, "firmware": 7 * DAY, "security": DAY, "system": DAY}
TINY_PROMPT = "Reply with the single word: ok"


def _settings() -> Any:
    from config import settings
    return settings


def _frozen() -> bool:
    import freeze
    return freeze.is_frozen()


def _record(what: str, **kw: Any) -> None:
    try:
        import changes
        changes.record_change(what, **kw)
    except Exception:
        pass


def _alert(title: str, body: str, ref: str = "") -> None:
    try:
        import alerts
        alerts.emit("system", title, body, ref=ref or None)
    except Exception:
        pass


# =====================================================================================  state
_lock = threading.Lock()


def state_path() -> Path:
    return Path(_settings().data_dir) / "self_updates.json"


def load_state() -> dict[str, Any]:
    try:
        st = json.loads(state_path().read_text(encoding="utf-8"))
        st = st if isinstance(st, dict) else {}
    except (OSError, ValueError):
        st = {}
    st.setdefault("vm_test", True)
    st.setdefault("last", {})
    st.setdefault("firmware", [])
    st.setdefault("security_waiting", 0)
    return st


def update_state(fn: Callable[[dict[str, Any]], None]) -> dict[str, Any]:
    with _lock:
        st = load_state()
        fn(st)
        p = state_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(st, indent=1, default=str), encoding="utf-8")
        os.replace(tmp, p)
        return st


# =====================================================================================  AI models
def _ollama(cfg: Any = None) -> str:
    cfg = cfg or _settings()
    return str(getattr(cfg, "ollama_url", "") or "http://127.0.0.1:11434").rstrip("/")


def _digest(h: Any, url: str, model: str) -> Optional[str]:
    tags = h.http_json("GET", f"{url}/api/tags", None, 5)
    if not isinstance(tags, dict):
        return None
    for m in tags.get("models") or []:
        if isinstance(m, dict) and m.get("name") in (model, f"{model}:latest"):
            return str(m.get("digest") or "present")
    return ""


def _answers(h: Any, url: str, model: str) -> bool:
    r = h.http_json("POST", f"{url}/api/generate", {"model": model, "prompt": TINY_PROMPT, "stream": False,
                                                    "options": {"num_predict": 8}}, 180)
    return isinstance(r, dict) and bool(str(r.get("response") or "").strip())


def backup_name(model: str) -> str:
    base = model.rsplit(":", 1)[0] if ":" in model.split("/")[-1] else model
    return f"{base}:orion-prev"


async def update_model(daemon: Any, why: str = "weekly check", approved_by: str = "automatic",
                       model: Optional[str] = None, h: Any = None) -> dict[str, Any]:
    """Pull the configured Ollama model with a check before and after. Puts the old one back if the new one
    doesn't answer. {"ok", "message", "changed", "rolled_back"}"""
    if _frozen():
        import freeze
        return {"ok": False, "message": freeze.MESSAGE}
    h = h or self_host.host()
    cfg = _settings()
    model = model or str(getattr(cfg, "ollama_model", "") or "")
    url = _ollama(cfg)
    if not model:
        return {"ok": False, "message": "No local model is set (Settings > Orion)."}
    before = await asyncio.to_thread(_digest, h, url, model)
    if before is None:
        return {"ok": False, "message": "Ollama isn't answering, so the model wasn't updated."}
    worked_before = bool(before) and await asyncio.to_thread(_answers, h, url, model)
    backup = backup_name(model)
    if before:
        await asyncio.to_thread(h.http_json, "POST", f"{url}/api/copy", {"source": model, "destination": backup}, 120)
    pulled = await asyncio.to_thread(h.http_json, "POST", f"{url}/api/pull", {"model": model, "stream": False}, 7200)
    if not isinstance(pulled, dict) or str(pulled.get("status", "")).lower() != "success":
        return {"ok": False, "message": f"Couldn't download {model} (no network, or the name is wrong)."}
    after = await asyncio.to_thread(_digest, h, url, model)
    if before and after == before:
        return {"ok": True, "changed": False, "message": f"{model} is already the newest version."}
    works = await asyncio.to_thread(_answers, h, url, model)
    tests = [{"name": "answers a small question", "before": worked_before, "after": works, "passed": works}]
    if not works and before:
        await asyncio.to_thread(h.http_json, "POST", f"{url}/api/copy", {"source": backup, "destination": model}, 120)
        _record(f"Tried a new version of the AI model {model} and put the old one back", why=why,
                kind="model_update", source="self-updates", approved_by=approved_by, tests=tests,
                result="rolled back", details={"model": model, "digest": after})
        _alert("AI model update didn't work", f"The new {model} didn't answer, so Orion put the old one back.",
               "model-update")
        return {"ok": False, "changed": False, "rolled_back": True,
                "message": f"The new {model} didn't answer, so the old one is back."}
    undo = {"kind": "model_restore", "label": "Put back the previous model", "model": model, "backup": backup} \
        if before else None
    _record(f"Updated the AI model {model}" if before else f"Downloaded the AI model {model}", why=why,
            kind="model_update", source="self-updates", approved_by=approved_by, tests=tests,
            result="ok" if works else "downloaded, not answering yet", undo=undo,
            details={"model": model, "from": before, "to": after})
    return {"ok": works, "changed": True, "fixed": works,
            "message": (f"Updated {model}; it answers fine." if before else f"Downloaded {model}.") if works else
            f"Downloaded {model}, but it doesn't answer yet."}


async def _undo_model(daemon: Any, change: dict[str, Any], undo: dict[str, Any]) -> dict[str, Any]:
    h = self_host.host()
    url = _ollama()
    r = await asyncio.to_thread(h.http_json, "POST", f"{url}/api/copy",
                                {"source": str(undo.get("backup")), "destination": str(undo.get("model"))}, 120)
    ok = r is not None
    return {"ok": ok, "message": "The previous model is back." if ok else "Ollama didn't answer; nothing changed."}


try:
    import changes as _changes
    _changes.register_undo("model_restore", _undo_model)
except Exception:
    pass


async def models_job(daemon: Any) -> dict[str, Any]:
    d = self_levels.decide("ai_model")
    model = str(getattr(_settings(), "ollama_model", "") or "")
    if not model:
        return {"skipped": "no model set"}
    if d == "ask":
        card = await self_cards.propose(daemon, "ai_model", f"Update the AI model {model}?",
                                        f"Orion checks for a newer {model} once a week. It asks the model a small "
                                        "question before and after, and puts the old one back if the new one "
                                        "doesn't answer.", "model_update", {"model": model}, key=f"model:{model}",
                                        proposer="Updates")
        return {"card": bool(card)}
    res = await update_model(daemon, approved_by=f"automatic ({self_levels.LEVEL_NAMES[self_levels.current().level('ai_model')].lower()})")
    if d == "tell" and res.get("changed"):
        _alert("AI model updated" if res.get("ok") else "AI model update", str(res.get("message")), "model-update")
    return res


# =====================================================================================  firmware
def parse_fwupd(text: str) -> list[dict[str, str]]:
    try:
        data = json.loads(text)
    except ValueError:
        return []
    out = []
    for dev in (data.get("Devices") or []) if isinstance(data, dict) else []:
        rels = dev.get("Releases") or []
        if not rels:
            continue
        out.append({"device": str(dev.get("Name") or "a device")[:80], "from": str(dev.get("Version") or "")[:40],
                    "to": str((rels[0] or {}).get("Version") or "")[:40]})
    return out


async def firmware_job(daemon: Any, h: Any = None) -> dict[str, Any]:
    """List firmware updates as a card. Always asks (level 1, can't be raised); Orion never flashes firmware."""
    h = h or self_host.host()
    if not h.which("fwupdmgr"):
        return {"skipped": "fwupd isn't installed"}
    await asyncio.to_thread(h.run, ["fwupdmgr", "refresh", "--assume-yes"], 300)   # fine if it says "no change"
    rc, out = await asyncio.to_thread(h.run, ["fwupdmgr", "get-updates", "--json"], 120)
    ups = parse_fwupd(out) if out.strip().startswith("{") else []
    update_state(lambda s: s.__setitem__("firmware", ups))
    if not ups:
        return {"firmware": []}
    names = "; ".join(f"{u['device']} {u['from']} → {u['to']}" for u in ups[:5])
    card = await self_cards.propose(daemon, "firmware", f"{len(ups)} firmware update{'s' if len(ups) != 1 else ''} "
                                    "waiting", f"{names}\n\nOrion never flashes firmware by itself. Approve to open "
                                    "the firmware updater in a terminal; keep the charger plugged in.",
                                    "firmware_open", {"count": len(ups)}, key="firmware",
                                    proposer="Updates")
    return {"firmware": ups, "card": bool(card)}


async def _action_firmware(daemon: Any, args: dict[str, Any]) -> dict[str, Any]:
    opened = await asyncio.to_thread(self_host.host().open_terminal, "fwupdmgr update")
    _record("Opened the firmware updater", kind="firmware", source="self-updates", approved_by="you (card)",
            result="terminal opened" if opened else "couldn't open a terminal")
    return {"ok": opened, "message": "The firmware updater is open in a terminal." if opened else
            "Couldn't open a terminal. Run: fwupdmgr update"}


# =====================================================================================  security updates
def count_security(apt_out: str) -> int:
    return sum(1 for line in apt_out.splitlines() if "-security" in line and "/" in line)


async def security_job(daemon: Any, h: Any = None) -> dict[str, Any]:
    """At "always ask" the gate stops unattended-upgrades, so look and ask. Otherwise Ubuntu's timer does it."""
    d = self_levels.decide("security_update")
    if d != "ask":
        return {"automatic": True}
    h = h or self_host.host()
    rc, out = await asyncio.to_thread(h.run, ["apt", "list", "--upgradable"], 120)
    n = count_security(out) if rc == 0 else 0
    update_state(lambda s: s.__setitem__("security_waiting", n))
    if not n:
        return {"security_waiting": 0}
    card = await self_cards.propose(daemon, "security_update", f"{n} security update{'s' if n != 1 else ''} waiting",
                                    "A snapshot is taken first, and Orion checks its health after. Approve to "
                                    "install them in a terminal (asks for the orion-admin password).",
                                    "security_install", {"count": n}, key="security", proposer="Updates")
    return {"security_waiting": n, "card": bool(card)}


async def _action_security(daemon: Any, args: dict[str, Any]) -> dict[str, Any]:
    cmd = "su -P - orion-admin -c 'sudo unattended-upgrade -v' && orion-selftest after-update security_update"
    opened = await asyncio.to_thread(self_host.host().open_terminal, cmd)
    return {"ok": opened, "message": "Installing in a terminal (it asks for the orion-admin password)." if opened
            else f"Couldn't open a terminal. Run: {cmd}"}


def _apt_summary(h: Any) -> str:
    p = "/var/log/unattended-upgrades/unattended-upgrades.log"
    try:
        lines = Path(p).read_text(encoding="utf-8", errors="replace").splitlines()[-80:]
    except OSError:
        return ""
    for line in reversed(lines):
        m = re.search(r"Packages that (?:will be|were) upgraded: (.+)$", line)
        if m:
            return m.group(1).strip()[:300]
    return ""


async def after_update(daemon: Any, kind: str = "security_update") -> dict[str, Any]:
    """Called by the root hook after unattended-upgrades (or a manual security install) ran."""
    kind = kind if kind in self_levels.KINDS else "security_update"
    mon = getattr(daemon, "health", None)
    before = (getattr(mon, "last", None) or {}).get("checks") or []
    before_fails = {c["id"] for c in before if c.get("status") == "fail"}
    after_fails: set[str] = set()
    if mon is not None:
        try:
            snap = await mon.check_now()
            after_fails = {c["id"] for c in snap.get("checks") or [] if c.get("status") == "fail"}
        except Exception:
            log.exception("health check after an update failed")
    new = sorted(after_fails - before_fails)
    pkgs = await asyncio.to_thread(_apt_summary, self_host.host())
    tests = [{"name": "health check after", "passed": not new, "new_problems": new}]
    label = self_levels.KINDS[kind].label
    try:
        import snapshots
        undo_to = (await asyncio.to_thread(snapshots.Snapshots().status)).get("undo_to")
    except Exception:
        undo_to = None
    undo = {"kind": "snapshot_restore", "snapshot": str(undo_to), "label": "Go back to before this update",
            "sensitive": True} if undo_to else None
    _record(f"Installed {label.lower()}" + (f": {pkgs}" if pkgs else ""), why="kept current automatically",
            kind=kind, source="self-updates", approved_by=f"automatic ({self_levels.LEVEL_NAMES[self_levels.current().level(kind)].lower()})",
            tests=tests, result="ok" if not new else "something broke after", undo=undo)
    if new:
        names = ", ".join(new)
        _alert("An update may have broken something", f"After {label.lower()}, these checks fail: {names}. "
               "Orion can put the system back to just before the update (asks for the admin password).", "after-update")
        if undo_to:
            await self_cards.propose(daemon, "security_update", "Go back to before the last update?",
                                     f"After {label.lower()} these checks started failing: {names}. Approving asks "
                                     "your phone, then the admin password, and the computer goes back at the next "
                                     "restart.", "snapshot_undo", {"snapshot": str(undo_to)}, key="after-update",
                                     proposer="Updates")
        return {"ok": False, "new_problems": new}
    if self_levels.decide(kind) == "tell":
        _alert(f"{label} installed", (pkgs or "Updates installed") + ". Everything still checks out.", "after-update")
    return {"ok": True, "new_problems": []}


async def _action_snapshot_undo(daemon: Any, args: dict[str, Any]) -> dict[str, Any]:
    import snapshots
    try:
        return await snapshots.request_restore(daemon, snapshots.Snapshots(), str(args.get("snapshot", "")),
                                               why="an update broke something", asked_by="self-updates")
    except (ValueError, LookupError, RuntimeError) as exc:
        return {"ok": False, "message": str(exc)}


async def _action_model(daemon: Any, args: dict[str, Any]) -> dict[str, Any]:
    return await update_model(daemon, why="you approved it", approved_by="you (card)",
                              model=str(args.get("model") or "") or None)


self_cards.register("model_update", _action_model)
self_cards.register("firmware_open", _action_firmware)
self_cards.register("security_install", _action_security)
self_cards.register("snapshot_undo", _action_snapshot_undo)


# =====================================================================================  the scheduler
class SelfUpdates:
    def __init__(self, daemon: Any, every_s: float = TICK_S) -> None:
        self.daemon = daemon
        self.every_s = every_s
        self._task: Optional[asyncio.Task] = None
        self._busy = asyncio.Lock()

    async def run_job(self, job: str) -> dict[str, Any]:
        if _frozen():
            return {"job": job, "skipped": "frozen"}
        async with self._busy:
            try:
                if job == "models":
                    res = await models_job(self.daemon)
                elif job == "firmware":
                    res = await firmware_job(self.daemon)
                elif job == "security":
                    res = await security_job(self.daemon)
                elif job == "system":
                    import update_channels
                    res = await update_channels.check_and_offer(self.daemon)
                else:
                    raise KeyError(job)
            finally:
                now = self_host.host().now()
                update_state(lambda s: s["last"].__setitem__(job, now))
        return {"job": job, **(res or {})}

    async def tick(self) -> list[dict[str, Any]]:
        if _frozen():
            return []
        now = self_host.host().now()
        last = load_state()["last"]
        out = []
        for job, every in JOBS.items():
            if now - float(last.get(job) or 0) >= every:
                try:
                    out.append(await self.run_job(job))
                except Exception:
                    log.exception("update job %s failed", job)
        return out

    async def _loop(self) -> None:
        await asyncio.sleep(300)      # not right at start-up
        while True:
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("update tick failed")
            await asyncio.sleep(self.every_s)

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.get_running_loop().create_task(self._loop(), name="self-updates")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
            self._task = None


def status() -> dict[str, Any]:
    st = load_state()
    kinds = {k: {"label": self_levels.KINDS[k].label, "decision": self_levels.decide(k)}
             for k in ("security_update", "app_update", "ai_model", "firmware", "system_version")}
    return {"vm_test": bool(st["vm_test"]), "last": st["last"], "firmware": st["firmware"],
            "security_waiting": st["security_waiting"], "kinds": kinds,
            "browser": "Firefox comes from apt, so it's updated with the system packages.",
            "apps": "Get apps updates Flatpak apps weekly and asks Orion's level first."}
