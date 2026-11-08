"""Self-repair loop (docs/ORIONOS_COMPLETE_PLAN.md Part 3 item 2).

Every couple of minutes it reads the health monitor's latest checks. For each warning or failure with a fix id:

  1. A known fix (FIXES below): its permission kind decides. "do"/"tell" (routine fixes are level 3 by default)
     run it now: a snapshot first when the fix changes files, then the fix, then the same health check again.
     If the check got worse, the fix is undone where it can be, and you get a card instead of another try.
     "ask" puts a card up. Hints (plug in the charger, open the phone app) are cards or notes, never actions.
  2. Something Orion has no fix for, or one that keeps coming back: it gathers diagnostics (journal excerpts,
     with anything that looks like a secret, an e-mail address or a hardware address taken out) and, when a model
     is set up, asks it for a diagnosis and a proposed fix. A config/script fix is tried in a Clean Box (a
     throwaway Ubuntu container) first. Then it's *proposed* as a card: unknown fixes are never applied by
     themselves, and approving one only opens a terminal that shows the script and asks once more.

Everything it does goes into the change history. Nothing runs while Orion is frozen.

HTTP (local):  GET /self/repair  (recent problems, what was tried)   POST /self/repair/run  (one round now)
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shlex
import shutil
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional

import self_cards
import self_host
import self_levels

log = logging.getLogger("orion.self_repair")

REPAIR_EVERY_S = 120.0
SETTLE_S = 8.0                 # wait after a fix before checking again
RETRY_GAP_S = 30 * 60          # the same fix runs by itself at most once per half hour...
MAX_AUTO_PER_DAY = 3           # ...and three times a day; after that it becomes a card
DIAGNOSE_GAP_S = 6 * 3600
CARD_GAP_S = 24 * 3600         # an answered card for the same problem doesn't come back for a day
LOG_AGE_DAYS = 14
RANK = {"ok": 0, "warn": 1, "fail": 2}

Ctx = Any   # the SelfRepair instance


@dataclass
class Fix:
    id: str
    kind: str                                   # a self_levels kind (or "hint")
    words: str                                  # what it does, in plain words
    run: Optional[Callable[[Ctx, dict[str, Any]], Awaitable[dict[str, Any]]]] = None
    auto: bool = True                           # may run without a card when the level allows
    snapshot: bool = False                      # take a snapshot first
    undo: Optional[Callable[[Ctx, dict[str, Any], dict[str, Any]], Awaitable[dict[str, Any]]]] = None
    card: bool = True                           # hints: show a card (False: only noted for the weekly review)


# =====================================================================================  the known fixes
async def _restart_units(ctx: Ctx, check: dict[str, Any]) -> dict[str, Any]:
    h = ctx.host
    done, skipped, failed = [], [], []
    for raw in check.get("detail") or []:
        raw = str(raw)
        mine = raw.endswith(" (yours)")
        unit = raw.replace(" (yours)", "").strip()
        if mine and self_host.user_unit_allowed(unit):
            rc, out = await asyncio.to_thread(h.run, ["systemctl", "--user", "restart", unit], 60)
        elif not mine and self_host.system_unit_allowed(unit):
            rc, out = await asyncio.to_thread(h.helper, "restart-unit", unit)
        else:
            skipped.append(unit)
            continue
        (done if rc == 0 else failed).append(unit)
    if not done and not failed:
        return {"ok": False, "message": "None of the stopped services are ones Orion may restart by itself: "
                + ", ".join(skipped[:4]) + ".", "skipped": skipped}
    msg = (f"Restarted {', '.join(done)}." if done else "") + \
          (f" Couldn't restart {', '.join(failed)}." if failed else "") + \
          (f" Left alone: {', '.join(skipped[:3])}." if skipped else "")
    return {"ok": bool(done) and not failed, "message": msg.strip(), "restarted": done}


async def _restart_hud(ctx: Ctx, check: dict[str, Any]) -> dict[str, Any]:
    h = ctx.host
    rc, _ = await asyncio.to_thread(h.run, ["pgrep", "-f", "orion-hud"], 10)
    if rc == 0:   # the orion-hud loop starts the screen again by itself
        rc, out = await asyncio.to_thread(h.run, ["pkill", "-x", "-f", "/usr/bin/orion|/usr/bin/Orion|"
                                                  "/opt/orion/hud/orion"], 10)
        return {"ok": rc in (0, 1), "message": "Restarted the Orion screen."}
    rc, out = await asyncio.to_thread(h.run, ["swaymsg", "exec", "orion-hud"], 10)
    return {"ok": rc == 0, "message": "Started the Orion screen." if rc == 0 else
            "Couldn't start the Orion screen (is the desktop running?)."}


async def _restart_ollama(ctx: Ctx, check: dict[str, Any]) -> dict[str, Any]:
    h = ctx.host
    rc, _ = await asyncio.to_thread(h.run, ["systemctl", "cat", "ollama.service"], 10)
    if rc == 0:
        rc, out = await asyncio.to_thread(h.helper, "restart-unit", "ollama.service")
        return {"ok": rc == 0, "message": "Restarted Ollama (the local AI)." if rc == 0 else
                "Couldn't restart Ollama: " + out.strip()[-160:]}
    rc, _ = await asyncio.to_thread(h.run, ["systemctl", "--user", "cat", "ollama.service"], 10)
    if rc == 0:
        rc, out = await asyncio.to_thread(h.run, ["systemctl", "--user", "restart", "ollama.service"], 60)
        return {"ok": rc == 0, "message": "Restarted Ollama (the local AI)." if rc == 0 else "Couldn't restart Ollama."}
    return {"ok": False, "message": "Ollama isn't installed as a service here, so there's nothing to restart. "
            "Set up a model in Settings > Orion.", "not_applicable": True}


async def _restart_network(ctx: Ctx, check: dict[str, Any]) -> dict[str, Any]:
    rc, out = await asyncio.to_thread(ctx.host.helper, "restart-unit", "NetworkManager.service")
    return {"ok": rc == 0, "message": "Restarted the network service." if rc == 0 else
            "Couldn't restart the network service: " + out.strip()[-160:]}


def _dir_size(p: Path) -> int:
    total = 0
    for root, _dirs, files in os.walk(p, onerror=lambda e: None):
        for f in files:
            try:
                total += os.lstat(os.path.join(root, f)).st_size
            except OSError:
                pass
    return total


def _clear_dir(p: Path) -> int:
    """Empty a cache folder (keeps the folder). Returns bytes freed."""
    freed = 0
    if not p.is_dir() or p.is_symlink():
        return 0
    for child in list(p.iterdir()):
        try:
            if child.is_dir() and not child.is_symlink():
                freed += _dir_size(child)
                shutil.rmtree(child, ignore_errors=True)
            else:
                freed += child.lstat().st_size
                child.unlink()
        except OSError:
            continue
    return freed


def _old_logs(d: Path, now: float, days: int = LOG_AGE_DAYS) -> int:
    freed = 0
    if not d.is_dir():
        return 0
    for f in d.rglob("*"):
        try:
            if f.is_file() and not f.is_symlink() and re.search(r"\.log(\.\d+)?(\.gz)?$", f.name) \
                    and now - f.stat().st_mtime > days * 86400:
                freed += f.stat().st_size
                f.unlink()
        except OSError:
            continue
    return freed


async def _free_space(ctx: Ctx, check: dict[str, Any]) -> dict[str, Any]:
    """Only things that grow back by themselves: Orion's caches, picture thumbnails, Orion logs older than two
    weeks, and the system journal trimmed to 200 MB. Never your files or the bin."""
    home = ctx.home
    cache = Path(os.environ.get("XDG_CACHE_HOME") or home / ".cache")
    now = ctx.host.now()
    freed = await asyncio.to_thread(lambda: _clear_dir(cache / "orion") + _clear_dir(cache / "thumbnails")
                                    + _old_logs(ctx.data_dir, now))
    rc, _ = await asyncio.to_thread(ctx.host.helper, "vacuum-journal")
    mb = freed / 1024 ** 2
    return {"ok": True, "freed_bytes": freed,
            "message": f"Cleared Orion's caches, thumbnails and old logs ({mb:.0f} MB)"
                       + (" and trimmed the system journal." if rc == 0 else ".")}


async def _pull_model(ctx: Ctx, check: dict[str, Any]) -> dict[str, Any]:
    import self_updates
    return await self_updates.update_model(ctx.daemon, why="the health check found the model missing",
                                           approved_by=ctx.approved_by)


async def _open_terminal(ctx: Ctx, command: str, ok_words: str) -> dict[str, Any]:
    opened = await asyncio.to_thread(ctx.host.open_terminal, command)
    return {"ok": opened, "message": ok_words if opened else f"Couldn't open a terminal. Run this yourself: {command}"}


async def _restart_daemon(ctx: Ctx, check: dict[str, Any]) -> dict[str, Any]:
    return await _open_terminal(ctx, "systemctl --user restart orion-daemon && echo 'Orion is restarting.'",
                                "Restarting Orion's brain (it's back in a few seconds).")


async def _install_voice(ctx: Ctx, check: dict[str, Any]) -> dict[str, Any]:
    return await _open_terminal(ctx, "orion-ctl setup voice", "Installing voice in a terminal window.")


FIXES: dict[str, Fix] = {f.id: f for f in (
    Fix("restart_unit", "routine_fix", "Restart the service that stopped", _restart_units),
    Fix("restart_hud", "routine_fix", "Restart the Orion screen", _restart_hud),
    Fix("setup_ai", "routine_fix", "Restart Ollama (the local AI)", _restart_ollama),
    Fix("check_network", "routine_fix", "Restart the network service", _restart_network),
    Fix("free_space", "routine_fix", "Clear Orion's caches, thumbnails and old logs", _free_space),
    Fix("pull_model", "ai_model", "Download the AI model Orion is set to use", _pull_model),
    Fix("restart_daemon", "routine_fix", "Restart Orion's brain", _restart_daemon, auto=False),
    Fix("install_voice", "app_update", "Install voice for Orion", _install_voice, auto=False),
    Fix("pair_phone", "hint", "Pair your phone again: Settings > Orion > Pair a phone, or orion-ctl pair-phone"),
    Fix("open_phone_app", "hint", "Open Orion on your phone so it reconnects (if it still doesn't, pair it "
        "again with orion-ctl pair-phone)"),
    Fix("restart_desktop", "hint", "Log out and back in, or restart the computer"),
    Fix("close_apps", "hint", "Close some apps or browser tabs", card=False),
    Fix("cool_down", "hint", "Let the computer cool down; check the vents", card=False),
    Fix("plug_in", "hint", "Plug in the charger", card=False),
    Fix("replace_battery", "hint", "The battery has worn down", card=False),
)}
IGNORED = {"unfreeze"}


# =====================================================================================  diagnostics
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_MAC = re.compile(r"\b[0-9A-Fa-f]{2}(:[0-9A-Fa-f]{2}){5}\b")


def redact_logs(text: str) -> str:
    try:
        from mcp_tools import redact
        text = redact(text)
    except Exception:
        pass
    text = _EMAIL.sub("[e-mail]", text)
    return _MAC.sub("[hw-address]", text)[-6000:]


async def gather_diagnostics(h: Any, check: dict[str, Any]) -> str:
    parts = [f"Check: {check.get('label')} = {check.get('status')}: {check.get('reason')}"]
    if check.get("detail"):
        parts.append(f"Detail: {json.dumps(check.get('detail'))[:600]}")
    for title, argv in (("Your session (warnings)", ["journalctl", "--user", "-p", "warning", "-n", "60",
                                                      "--no-pager", "-o", "short"]),
                        ("System (errors)", ["journalctl", "-p", "err", "-b", "-n", "60", "--no-pager", "-o",
                                             "short"])):
        rc, out = await asyncio.to_thread(h.run, argv, 20)
        if rc == 0 and out.strip():
            parts.append(f"--- {title} ---\n{out.strip()[-3000:]}")
    return redact_logs("\n".join(parts))


DIAG_SYSTEM = ("You are the repair helper of OrionOS (Ubuntu 24.04, Sway desktop, a Python daemon called Orion). "
               "Read the failing health check and the log lines. Answer ONLY with JSON: {\"diagnosis\": one or two "
               "plain sentences, \"fix_kind\": \"config\" | \"script\" | \"manual\" | \"none\", \"script\": a short "
               "bash script for config/script fixes (no sudo unless needed, no deleting user files, no network "
               "downloads), \"explain\": one plain sentence saying what the script does}.")


def _parse_json(text: str) -> Optional[dict[str, Any]]:
    if not text:
        return None
    m = re.search(r"\{.*\}", text, re.S)
    try:
        d = json.loads(m.group(0) if m else text)
        return d if isinstance(d, dict) else None
    except ValueError:
        return None


async def default_ask_model(daemon: Any, system: str, user: str) -> Optional[str]:
    orch = getattr(daemon, "orchestrator", None)
    if orch is None or not hasattr(orch, "frontier_text"):
        return None
    try:
        text, _model = await orch.frontier_text(system, user, json_mode=True, max_tokens=800, timeout=90)
        return text
    except Exception:
        return None


def default_box() -> Any:
    from clean_box import CleanBox
    return CleanBox()


# =====================================================================================  the loop
class SelfRepair:
    def __init__(self, daemon: Any, data_dir: Path, host: Optional[Any] = None,
                 ask_model: Callable[[Any, str, str], Awaitable[Optional[str]]] = default_ask_model,
                 box_factory: Callable[[], Any] = default_box, every_s: float = REPAIR_EVERY_S,
                 settle_s: float = SETTLE_S, home: Optional[Path] = None) -> None:
        self.daemon = daemon
        self.data_dir = Path(data_dir)
        self._host = host
        self.ask_model = ask_model
        self.box_factory = box_factory
        self.every_s = every_s
        self.settle_s = settle_s
        self.home = Path(home) if home else Path.home()
        self.path = self.data_dir / "self_repair.json"
        self.lock = threading.Lock()
        self.approved_by = "automatic"
        self._task: Optional[asyncio.Task] = None
        self._round = asyncio.Lock()
        self._last_status: dict[str, str] = {}

    @property
    def host(self) -> Any:
        return self._host or self_host.host()

    # ------------------------------------------------------------------ state
    def state(self) -> dict[str, Any]:
        try:
            st = json.loads(self.path.read_text(encoding="utf-8"))
            st = st if isinstance(st, dict) else {}
        except (OSError, ValueError):
            st = {}
        for k, v in (("seen", []), ("attempts", {}), ("blocked", {}), ("diagnosed", {}), ("tried", [])):
            st.setdefault(k, v)
        return st

    def _save(self, st: dict[str, Any]) -> None:
        st["seen"] = st["seen"][-500:]
        st["tried"] = st["tried"][-300:]
        self.data_dir.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(st, indent=1, default=str), encoding="utf-8")
        os.replace(tmp, self.path)

    def _update(self, fn: Callable[[dict[str, Any]], None]) -> None:
        with self.lock:
            st = self.state()
            fn(st)
            self._save(st)

    def monitor(self) -> Any:
        return getattr(self.daemon, "health", None)

    # ------------------------------------------------------------------ one round
    async def run_once(self) -> list[dict[str, Any]]:
        """Look at the latest health checks and act on them. Returns what was done (for tests and the route)."""
        import freeze
        if freeze.is_frozen():
            return [{"skipped": "frozen"}]
        mon = self.monitor()
        if mon is None:
            return []
        async with self._round:
            snap = getattr(mon, "last", None) or {}
            checks = list(snap.get("checks") or [])
            out = []
            now = self.host.now()
            for c in checks:
                st = c.get("status")
                if self._last_status.get(c["id"]) != st and st in ("warn", "fail"):
                    self._update(lambda s, c=c: s["seen"].append({"ts": now, "check": c["id"], "label": c.get("label"),
                                                                  "status": c["status"], "reason": c.get("reason"),
                                                                  "fix": c.get("fix")}))
                self._last_status[c["id"]] = st
                if st not in ("warn", "fail") or c.get("fix") in IGNORED:
                    continue
                try:
                    res = await self.handle(c)
                except Exception:
                    log.exception("self-repair of %s failed", c.get("id"))
                    continue
                if res:
                    out.append(res)
            return out

    def _auto_allowed(self, fix_id: str, now: float) -> bool:
        st = self.state()
        if float(st["blocked"].get(fix_id) or 0) > now:
            return False
        tries = [t for t in st["attempts"].get(fix_id, []) if now - t < 86400]
        if len(tries) >= MAX_AUTO_PER_DAY:
            return False
        return not tries or now - max(tries) >= RETRY_GAP_S

    async def handle(self, check: dict[str, Any]) -> Optional[dict[str, Any]]:
        fix = FIXES.get(str(check.get("fix") or ""))
        now = self.host.now()
        if fix is None:
            if check.get("status") == "fail":
                return await self.diagnose(check)
            return None
        if fix.kind == "hint":
            if not fix.card:
                return None
            card = await self._card(check, fix, "hint", "repair_hint",
                                    f"{check.get('label')}: {fix.words.split(':')[0].split(' (')[0]}",
                                    f"{check.get('reason')}\n\nWhat to do: {fix.words}.\n\nApprove = done (I've "
                                    "seen it). Reject = not now.")
            return {"check": check["id"], "fix": fix.id, "card": bool(card), "hint": True}
        decision = self_levels.decide(fix.kind)
        if decision == "ask" or not fix.auto or not self._auto_allowed(fix.id, now):
            if fix.auto and decision != "ask" and not self._auto_allowed(fix.id, now):
                if check.get("status") == "fail":   # it keeps coming back: look deeper before asking again
                    await self.diagnose(check)
            card = await self._card(check, fix, fix.kind, "repair_fix", f"{check.get('label')}: {fix.words}?",
                                    f"{check.get('reason')}\n\nOrion would: {fix.words.lower()}, then check again "
                                    "and undo it if things got worse.")
            return {"check": check["id"], "fix": fix.id, "card": bool(card), "decision": decision}
        self._update(lambda s: s["attempts"].setdefault(fix.id, []).append(now))
        res = await self.apply(fix, check, approved_by=f"automatic ({self_levels.LEVEL_NAMES[self_levels.current().level(fix.kind)].lower()})")
        if decision == "tell":
            try:
                import alerts
                alerts.emit("system", f"Orion fixed: {check.get('label')}" if res.get("fixed") else
                            f"Orion tried to fix: {check.get('label')}", str(res.get("message", ""))[:300],
                            ref=f"repair:{fix.id}")
            except Exception:
                pass
        return {"check": check["id"], "fix": fix.id, "ran": True, **res}

    async def _card(self, check: dict[str, Any], fix: Fix, change_kind: str, action: str, title: str,
                    body: str, suffix: str = "") -> Optional[dict[str, Any]]:
        """One card per problem; after you answer it, the same card doesn't come back for CARD_GAP_S."""
        key = f"repair:{fix.id}:{check['id']}{suffix}"
        waiting = self_cards.waiting(self.daemon, key)
        if waiting is not None:
            return waiting
        now = self.host.now()
        if now - float(self.state().setdefault("carded", {}).get(key) or 0) < CARD_GAP_S:
            return None
        try:
            card = await self_cards.propose(self.daemon, change_kind, title, body, action,
                                            {"fix": fix.id, "check": check}, key=key, proposer="Self-repair")
        except Exception:
            log.exception("couldn't show the repair card")
            return None
        if card is not None:
            self._update(lambda s: s.setdefault("carded", {}).__setitem__(key, now))
        return card

    # ------------------------------------------------------------------ run a fix, check again, undo if worse
    async def apply(self, fix: Fix, check: dict[str, Any], approved_by: str) -> dict[str, Any]:
        import freeze
        if freeze.is_frozen():
            return {"ok": False, "fixed": False, "message": freeze.MESSAGE}
        before = check.get("status", "warn")
        mon = self.monitor()
        before_fails = sum(1 for c in (getattr(mon, "last", {}) or {}).get("checks") or [] if c["status"] == "fail")
        snap_id = None
        if fix.snapshot:
            try:
                import snapshots
                taken = await asyncio.to_thread(snapshots.Snapshots().take, f"before: {fix.words.lower()}")
                snap_id = taken.get("id") if taken.get("ok") else None
            except Exception:
                pass
        self.approved_by = approved_by
        res = await fix.run(self, check) if fix.run else {"ok": False, "message": "Nothing to run."}
        after, after_fails, worse = before, before_fails, False
        if mon is not None and not res.get("not_applicable"):
            await self.host.sleep(self.settle_s)
            try:
                snap = await mon.check_now()
                again = next((c for c in snap.get("checks") or [] if c["id"] == check["id"]), None)
                after = again["status"] if again else before
                after_fails = sum(1 for c in snap.get("checks") or [] if c["status"] == "fail")
            except Exception:
                log.exception("re-check after %s failed", fix.id)
            worse = RANK.get(after, 1) > RANK.get(before, 1) or after_fails > before_fails
        fixed = after == "ok"
        undone = None
        if worse:
            if fix.undo is not None:
                try:
                    undone = await fix.undo(self, check, res)
                except Exception:
                    undone = {"ok": False, "message": "The undo failed."}
            self._update(lambda s: s["blocked"].__setitem__(fix.id, self.host.now() + 86400))
        words = res.get("message") or fix.words
        if worse:
            words += " It made things worse" + (", so Orion undid it." if undone and undone.get("ok") else
                                               "; Orion won't try it by itself again today.")
        elif not fixed and res.get("ok"):
            words += " The check still isn't happy."
        result = "fixed" if fixed else "made worse" if worse else "ran" if res.get("ok") else "failed"
        tests = [{"name": f"{check.get('label')} check", "before": before, "after": after, "passed": not worse}]
        undo = None
        if res.get("undo") and not worse:
            undo = res["undo"]
        try:
            import changes
            changes.record_change(f"Self-repair: {fix.words}" + (f" ({check.get('label')})" if check.get("label") else ""),
                                  why=str(check.get("reason") or "")[:400], kind="repair", source="self-repair",
                                  approved_by=approved_by, tests=tests, result=result, undo=undo,
                                  details={"fix": fix.id, "check": check["id"], "snapshot": snap_id,
                                           "message": words[:300]})
        except Exception:
            pass
        self._update(lambda s: s["tried"].append({"ts": self.host.now(), "fix": fix.id, "check": check["id"],
                                                  "result": result, "by": approved_by}))
        if worse:
            await self._card(check, fix, fix.kind, "repair_fix", f"{check.get('label')}: the fix made it worse",
                             f"{words}\n\nApprove = try it once more anyway. Reject = leave it.", suffix=":worse")
        return {"ok": bool(res.get("ok")) and not worse, "fixed": fixed, "worse": worse, "after": after,
                "message": words, "undone": undone}

    # ------------------------------------------------------------------ unknown problems
    async def diagnose(self, check: dict[str, Any]) -> Optional[dict[str, Any]]:
        import freeze
        if freeze.is_frozen():
            return None
        now = self.host.now()
        if now - float(self.state()["diagnosed"].get(check["id"]) or 0) < DIAGNOSE_GAP_S:
            return None
        key = f"diagnose:{check['id']}"
        if self_cards.waiting(self.daemon, key) is not None:
            return None
        self._update(lambda s: s["diagnosed"].__setitem__(check["id"], now))
        logs = await gather_diagnostics(self.host, check)
        answer = await self.ask_model(self.daemon, DIAG_SYSTEM, logs)
        d = _parse_json(answer or "") or {}
        diagnosis = " ".join(str(d.get("diagnosis") or "").split())[:500]
        script = str(d.get("script") or "").strip()[:4000]
        kind = str(d.get("fix_kind") or "none")
        tried = None
        if script and kind in ("config", "script"):
            tried = await self._try_in_box(script)
        lines = [f"{check.get('label')}: {check.get('reason')}"]
        if diagnosis:
            lines.append(f"What Orion thinks: {diagnosis}")
        elif answer is None:
            lines.append("No AI model is set up (or it didn't answer), so this is only what the logs say.")
        if script:
            lines += ["", f"Proposed fix: {str(d.get('explain') or '').strip()[:300]}", "", script]
            if tried is None:
                pass
            elif tried.get("skipped"):
                lines.append(f"\nNot tried first: {tried['skipped']}")
            else:
                lines.append("\nTried in a Clean Box (a throwaway copy of Ubuntu): "
                             + ("it ran without errors." if tried["ok"] else "it FAILED there:\n" + tried["output"][-600:]))
        lines += ["", "Logs Orion looked at (secrets taken out):", logs[-1500:]]
        body = "\n".join(lines)
        action = "repair_review_script" if script else "repair_hint"
        title = f"{check.get('label')}: Orion looked into it" + (" and has a fix to show you" if script else "")
        try:
            card = await self_cards.propose(self.daemon, "unknown_fix", title, body, action,
                                            {"check": check, "script": script, "diagnosis": diagnosis,
                                             "box": tried}, key=key, proposer="Self-repair")
        except Exception:
            log.exception("couldn't show the diagnosis card")
            card = None
        try:
            import changes
            changes.record_change(f"Looked into a problem: {check.get('label')}", why=str(check.get("reason"))[:300],
                                  kind="diagnosis", source="self-repair", approved_by="automatic (only looked)",
                                  tests=[{"name": "tried in a Clean Box", "passed": bool(tried and tried.get("ok"))}]
                                  if tried and not tried.get("skipped") else [],
                                  result="proposed a fix" if script else "noted",
                                  details={"diagnosis": diagnosis, "has_script": bool(script)})
        except Exception:
            pass
        return {"check": check["id"], "diagnosed": True, "diagnosis": diagnosis, "script": bool(script),
                "box": tried, "card": bool(card)}

    async def _try_in_box(self, script: str) -> dict[str, Any]:
        try:
            box = self.box_factory()
            if not box.available():
                return {"skipped": "Clean Box needs Podman, which isn't installed here.", "ok": False}
            r = await asyncio.to_thread(box.run, script, (), 180)
            return {"ok": bool(r.ok), "rc": r.rc, "output": str(r.output)[-2000:]}
        except Exception as exc:
            return {"skipped": f"the Clean Box couldn't start ({type(exc).__name__})", "ok": False}

    # ------------------------------------------------------------------ lifecycle
    async def _loop(self) -> None:
        await asyncio.sleep(60)          # let the health monitor do its first round
        while True:
            try:
                await self.run_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("self-repair round failed")
            await asyncio.sleep(self.every_s)

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.get_running_loop().create_task(self._loop(), name="self-repair")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
            self._task = None

    def summary(self, since: float = 0.0) -> dict[str, Any]:
        st = self.state()
        return {"seen": [s for s in st["seen"] if float(s.get("ts") or 0) >= since][-50:],
                "tried": [t for t in st["tried"] if float(t.get("ts") or 0) >= since][-50:],
                "blocked": {k: v for k, v in st["blocked"].items() if float(v) > self.host.now()}}


# =====================================================================================  card actions
def _repair() -> Optional[SelfRepair]:
    import self_running
    return self_running.repair_or_none()


async def _action_fix(daemon: Any, args: dict[str, Any]) -> dict[str, Any]:
    fix = FIXES.get(str(args.get("fix", "")))
    rep = _repair()
    if fix is None or fix.run is None or rep is None:
        return {"ok": False, "message": "That fix isn't available any more."}
    check = dict(args.get("check") or {})
    check.setdefault("id", "unknown")
    return await rep.apply(fix, check, approved_by="you (card)")


async def _action_hint(daemon: Any, args: dict[str, Any]) -> dict[str, Any]:
    return {"ok": True, "message": "Noted."}


async def _action_review_script(daemon: Any, args: dict[str, Any]) -> dict[str, Any]:
    """Approving an unknown fix never runs it silently: a terminal shows the script and asks once more."""
    script = str(args.get("script") or "")
    if not script:
        return {"ok": False, "message": "There's no script in this card."}
    from config import settings
    d = Path(settings.data_dir) / "repair-scripts"
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"fix-{uuid.uuid4().hex[:8]}.sh"
    p.write_text("#!/bin/sh\n# Proposed by Orion's self-repair. Read it before you run it.\n" + script + "\n",
                 encoding="utf-8")
    os.chmod(p, 0o700)
    q = shlex.quote(str(p))
    cmd = (f"cat {q}; echo; printf 'Run this fix now? [y/N] '; read a; "
           f"if [ \"$a\" = y ]; then sh {q} && echo 'Done.'; else echo 'Not run.'; fi")
    host = (_repair().host if _repair() else self_host.host())
    opened = await asyncio.to_thread(host.open_terminal, cmd)
    try:
        import changes
        changes.record_change(f"Opened a proposed fix to run: {(args.get('check') or {}).get('label', '')}",
                              why=str(args.get("diagnosis") or "")[:300], kind="repair_proposal",
                              source="self-repair", approved_by="you (card)", result="shown in a terminal",
                              details={"script": str(p)})
    except Exception:
        pass
    return {"ok": opened, "message": "The fix is open in a terminal: read it, then say y to run it." if opened
            else f"Couldn't open a terminal. The script is in {p}."}


self_cards.register("repair_fix", _action_fix)
self_cards.register("repair_hint", _action_hint)
self_cards.register("repair_review_script", _action_review_script)
