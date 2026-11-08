"""Orion Test Center: one checklist of everything a person checks to trust Orion on the PC and the phone.

Open it with "Test Orion.cmd" (Windows), `orion-ctl test` or Super+T (OrionOS), the HUD's menu, or by saying
"Orion, test everything". Press "Run all automatic checks", then follow the one "Next" card.

Items are grouped simply (Start-up, Look, Voice, Phone, Agents, Projects, Skills, Safety, Self-change, OrionOS,
Windows). Each item:

    {"id", "title", "where": "pc"|"phone"|"both", "kind": "auto"|"manual",
     "status": "todo"|"running"|"pass"|"fail"|"skip", "detail", "next"}
    + "group", "action" (label of a helper the Run button does for a manual item, or null),
      "open" (a HUD window the item is about, or null), "note", "at"

"next" is one plain sentence saying exactly what to tap or say next.
    + "do" (the "Do it for me" button's label, or null when the PC can't do it for you), "ask" (a Yes / No question
      waiting for your answer, e.g. "Did you hear it?"), "watch" (how it ticks itself off), "armed" (its action ran
      and Orion is watching for the result), "by_phone" (the phone's Test screen fills it in).

"Do it for me" (test_center_auto.py) does the harmless half of a manual test and then watches: items tick
themselves off when Orion sees them really happen (the card was approved on the phone, a skill was saved, the ghost
preview is ready, an undo happened...). Watchers only read state and never fail an item you didn't start.

Rules for the automatic checks: they only read Orion's own modules on this computer. No network, nothing public,
nothing that can't be undone. The stand-in agent mission runs in a throwaway git repo in a temporary folder with
its own throwaway approval list, and the folder is deleted afterwards. Nothing is ever approved for you: the test
card for your fingerprint is only sent, you approve it on your phone yourself (approving it does nothing at all).

HTTP (main daemon, port 9120, with Orion's key):
    GET  /tests                 {"groups": [{"id","title","items":[...]}], "next": item|null,
                                 "summary": {"pass","fail","todo","skip"}, "running": bool}
    POST /tests/run-auto        {"fresh"?: bool} runs every automatic check in the background (status "running"
                                meanwhile); fresh=false (the window opening) only runs the ones not passed yet
    POST /tests/{id}/run        one automatic check, or a manual item's helper (say something, send a test card)
    POST /tests/{id}/result     {"status": "pass"|"fail"|"skip"|"todo", "note"?}
    POST /tests/reset           forget every result
    GET  /tests/report          {"markdown": "..."}  short and shareable: no keys, no names
    POST /tests/open            {"run"?: bool} ask the Orion window to open the Test Center (launchers, voice);
                                run=true also starts the automatic checks
    GET  /tests/open-request    {"open": bool, "run": bool}  the HUD asks once when it connects (and clears it)
Phone (port 9121, signed by the paired phone; phone_tests.py): GET /phone/tests, POST /phone/tests/{id}/result.
Results are kept in <data dir>/test_center.json.
"""
from __future__ import annotations

import asyncio
import getpass
import json
import logging
import os
import platform as _platform
import re
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional

from fastapi import APIRouter, Request  # module level: the routes use postponed annotations
from fastapi.responses import JSONResponse

import test_center_auto as auto

log = logging.getLogger("orion.test_center")

STATUSES = ("todo", "running", "pass", "fail", "skip")
SET_STATUSES = ("todo", "pass", "fail", "skip")
WHERE = ("pc", "phone", "both")
NOTE_MAX = 300
FACTS_MAX = 20
OPEN_TTL_S = 15 * 60
PHONE_RECENT_S = 15 * 60
CHECK_TIMEOUT_S = 20.0
DETECT_EVERY_S = 2.0
DETECT_TIMEOUT_S = 15.0
MISSION_TIMEOUT_S = 120.0
TEST_CARD_TASK = "orion.test_card"
HUD_OPEN_EVENT = "HUD_OPEN"

Check = Callable[["TestCenter"], Awaitable[tuple]]


@dataclass
class Item:
    id: str
    group: str
    title: str
    where: str
    kind: str
    detail: str
    next: str
    fix: str = ""                                     # the next sentence after a failure
    check: Optional[Check] = None                     # auto items: the real check
    action: Optional[tuple[str, Check]] = None        # manual items: (button label, helper)
    open: Optional[str] = None                        # HUD window the item is about
    only: Optional[Callable[["TestCenter"], bool]] = None   # shown only where this is true
    hud: bool = False                                 # checked by the Orion window itself (it posts the result)
    detect: Optional[Callable[["TestCenter"], Awaitable[Optional[tuple]]]] = None   # ticks it off when seen
    watch: str = ""                                   # plain words: how it ticks itself off
    by_phone: bool = False                            # the phone's own Test screen fills it in


GROUPS: list[tuple[str, str]] = [
    ("startup", "Start-up"), ("look", "Look"), ("voice", "Voice"), ("phone", "Phone"), ("agents", "Agents"),
    ("projects", "Projects"), ("skills", "Skills"), ("safety", "Safety"), ("selfchange", "Self-change"),
    ("orionos", "OrionOS"), ("windows", "Windows"),
]


# =====================================================================================  where am I
def detect_platform() -> str:
    """"orionos", "windows" or "other". ORION_TEST_PLATFORM overrides it (tests)."""
    env = os.environ.get("ORION_TEST_PLATFORM", "").strip().lower()
    if env in ("orionos", "windows", "other"):
        return env
    if sys.platform == "win32":
        return "windows"
    if sys.platform.startswith("linux") and (Path("/etc/orion/profile").is_file() or Path("/opt/orion/daemon").is_dir()):
        return "orionos"
    return "other"


def os_profile() -> str:
    """OrionOS profile: image (USB stick), baremetal, vm, or ''."""
    env = os.environ.get("ORION_TEST_PROFILE", "").strip().lower()
    if env:
        return env
    try:
        return Path("/etc/orion/profile").read_text(encoding="utf-8").strip().lower()
    except OSError:
        return ""


def _on_os(tc: "TestCenter") -> bool:
    return tc.platform == "orionos"


def _on_windows(tc: "TestCenter") -> bool:
    return tc.platform == "windows"


def _on_stick(tc: "TestCenter") -> bool:
    return tc.platform == "orionos" and tc.profile in ("image", "")


def _has_windows_too(tc: "TestCenter") -> bool:
    return tc.platform == "orionos" and bool(tc.which("orion-restart-windows"))


# =====================================================================================  automatic checks
async def _daemon_up(tc: "TestCenter") -> tuple:
    d = tc.daemon
    up = int(max(0, time.time() - float(getattr(d, "started_at", time.time()) or time.time())) // 60)
    ver = ""
    try:
        ver = str(__import__("main").VERSION) if "main" in sys.modules else ""
    except Exception:
        ver = ""
    tc.version = ver or tc.version
    return "pass", f"Orion's brain is running{f' (version {ver})' if ver else ''}, up {up} min."


async def _key_works(tc: "TestCenter") -> tuple:
    import api_token
    local = api_token.read_local()
    if not local:
        return "fail", "The key file local tools read isn't there.", "Restart Orion with Start Orion.cmd, then tap Run again."
    if not api_token.check(local):
        return "fail", "The key file is out of date (an old Orion may still be running).", \
            "Close every Orion window, start Orion again, then tap Run again."
    return "pass", "Local tools can read Orion's key, and Orion accepts it."


async def _hud_connected(tc: "TestCenter") -> tuple:
    bus = getattr(tc.daemon, "bus", None)
    clients = bus.clients_state() if bus is not None else []
    n = sum(1 for c in clients if c.get("kind") == "desktop")
    if n:
        return "pass", "The Orion window is connected to Orion's brain."
    return "fail", "No Orion window is connected.", "Start Orion (Start Orion.cmd on Windows), then tap Run again."


def _health_now(d: Any) -> dict[str, Any]:
    pp = getattr(d, "phone_pair", None)
    if pp is not None and hasattr(pp, "_health"):
        return pp._health()
    mon = getattr(d, "health", None)
    last = getattr(mon, "last", None) or {}
    level = {"ok": 0, "warn": 1, "fail": 2}.get(str(last.get("status") or "ok"), 0)
    return {"level": level, "text": str(last.get("words") or "Everything looks fine.")}


async def _health_light(tc: "TestCenter") -> tuple:
    h = _health_now(tc.daemon)
    level, text = int(h.get("level") or 0), str(h.get("text") or "")
    if level <= 1:
        return "pass", f"Health light: {['green', 'amber'][level]}. {text}".strip()
    return "fail", f"Health light: red. {text}".strip(), "Open Settings > Safety to see what's wrong."


async def _parts_ok(tc: "TestCenter") -> tuple:
    import module_guard
    bad = module_guard.failed()
    if not bad:
        return "pass", "Every part of Orion started."
    names = ", ".join(sorted(str(b.get("module")) for b in bad)[:6])
    return "fail", f"These parts are switched off because they failed to start: {names}.", \
        "Copy the report and share it with whoever fixes Orion."


async def _mic_found(tc: "TestCenter") -> tuple:
    stt = getattr(tc.daemon, "stt", None)
    if stt is not None and (stt.is_alive() if hasattr(stt, "is_alive") else True):
        return "pass", "Orion's voice typing is running and has a microphone."
    hm = getattr(tc.daemon, "hotmic", None)
    missing = []
    try:
        missing = list(hm.missing()) if hm is not None else []
    except Exception:
        missing = []
    why = f" Missing: {', '.join(map(str, missing[:4]))}." if missing else ""
    return "fail", f"Orion can't hear a microphone yet.{why}", \
        "Plug in or allow a microphone, restart Orion, then tap Run again."


async def _mic_off_default(tc: "TestCenter") -> tuple:
    import hotmic
    if hotmic.DEFAULTS.get("enabled") or hotmic.DEFAULTS.get("wake_word"):
        return "fail", "The hot mic or wake word would be on for a new user.", "Copy the report and share it."
    hm = getattr(tc.daemon, "hotmic", None)
    on = bool(getattr(hm, "listening", False)) if hm is not None else False
    wake = False
    try:
        wake = bool(hm.config().get("wake_word")) if hm is not None else False
    except Exception:
        wake = False
    if on or wake:
        return "pass", "Off for new users. It's on now because you turned it on (the mic light shows it)."
    return "pass", "Hot mic and wake word are off, as they should be until you turn them on."


def _phones(tc: "TestCenter") -> list[dict[str, Any]]:
    board = getattr(tc.daemon, "board", None)
    if board is None or getattr(board, "store", None) is None:
        return []
    try:
        raw = json.loads(board.store.get("phones", "{}") or "{}")
    except Exception:
        return []
    return [v for v in raw.values() if isinstance(v, dict)] if isinstance(raw, dict) else []


async def _phone_paired(tc: "TestCenter") -> tuple:
    n = len(_phones(tc))
    if n:
        return "pass", f"{n} phone{'s' if n != 1 else ''} paired."
    how = "run orion-ctl pair-phone" if tc.platform == "orionos" else "open Settings and choose Pair my phone"
    return "fail", "No phone is paired yet.", f"On the PC {how}, then scan the code with Orion Mobile."


async def _phone_reaches(tc: "TestCenter") -> tuple:
    phones = _phones(tc)
    if not phones:
        return "fail", "No phone is paired yet.", "Pair your phone first (the item above)."
    last = max(int(p.get("last_ts") or 0) for p in phones) / 1000.0
    if last and time.time() - last <= PHONE_RECENT_S:
        mins = int((time.time() - last) // 60)
        return "pass", f"Your phone reached this PC {('just now' if mins < 1 else f'{mins} min ago')}."
    return "fail", "Your phone hasn't reached this PC in the last 15 minutes.", \
        "Open Orion Mobile on your phone and pull down to refresh, then tap Run again."


class _SendSpy:
    """Counts reply attempts on the message hub while a draft is made (there must be none)."""

    def __init__(self, hub: Any) -> None:
        self.hub, self.calls, self._orig = hub, 0, None

    def __enter__(self) -> "_SendSpy":
        if self.hub is not None and hasattr(self.hub, "reply"):
            self._orig = self.hub.reply

            async def spy(*a: Any, **k: Any) -> Any:
                self.calls += 1
                return False, "blocked by the Test Center", None
            self.hub.reply = spy
        return self

    def __exit__(self, *exc: Any) -> None:
        if self._orig is not None:
            self.hub.reply = self._orig


async def _inbox_draft(tc: "TestCenter") -> tuple:
    pp = getattr(tc.daemon, "phone_pair", None)
    if pp is None or not hasattr(pp, "draft"):
        return "skip", "The phone connection part isn't running on this PC."
    with _SendSpy(getattr(tc.daemon, "comms", None)) as spy:
        try:
            draft = await asyncio.wait_for(pp.draft("messages", "Test", "Are we still on for tomorrow?", None), 25)
        except asyncio.TimeoutError:
            return "fail", "Making a draft took too long.", "Check Orion's brain in Settings, then tap Run again."
    if spy.calls:
        return "fail", "Something tried to send while a draft was made.", "Copy the report and share it."
    if not str(draft or "").strip():
        return "fail", "No draft came back.", "Check Orion's brain in Settings, then tap Run again."
    return "pass", "A test draft was written and nothing was sent. Sending always needs your tap."


async def _agents_found(tc: "TestCenter") -> tuple:
    found = [label for b, label in (("claude", "Claude Code"), ("codex", "Codex"), ("gemini", "Gemini CLI"))
             if tc.which(b)]
    if found:
        missing = [label for b, label in (("claude", "Claude Code"), ("codex", "Codex"), ("gemini", "Gemini CLI"))
                   if label not in found]
        more = f" Not installed: {', '.join(missing)}." if missing else ""
        return "pass", f"Found: {', '.join(found)}.{more}"
    return "fail", "No coding agent is installed (Claude Code, Codex or Gemini CLI).", \
        "Install Claude Code (or Codex or Gemini CLI), sign in once, then tap Run again."


async def _wifi(tc: "TestCenter") -> tuple:
    nm = tc.which("nmcli")
    if not nm:
        return "skip", "Network Manager isn't on this computer."
    try:
        out = await asyncio.to_thread(subprocess.run, [nm, "-t", "-f", "STATE", "general"], capture_output=True,
                                      text=True, timeout=10)
        st = (out.stdout or "").strip().lower()
    except Exception:
        st = ""
    if st.startswith("connected"):
        return "pass", "Connected to a network."
    return "fail", "Not connected to a network.", "Click the network icon in the top bar and pick your Wi-Fi."


# ------------------------------------------------------------------ the stand-in agent mission (dry run)
FAKE_RUN = ("open('ORION_TEST.txt', 'w').write('Made by Orion\\'s stand-in test agent.\\n')\n"
            "print('Wrote ORION_TEST.txt. This was a dry run with a stand-in agent.')")
FAKE_REVIEW = "print('Looks fine for a test. VERDICT: approve')"
FAKE_BIN = "orion-test-agent"


def _fake_adapters() -> dict[str, dict[str, Any]]:
    exe = sys.executable
    return {"fake": {"label": "Stand-in test agent", "bin": FAKE_BIN, "mode": "headless", "provider": "ollama",
                     "parser": "text", "price_model": "",
                     "run": ["{bin}", "-I", "-c", FAKE_RUN, "{prompt}"],
                     "review": ["{bin}", "-I", "-c", FAKE_REVIEW, "{prompt}"],
                     "model_flag": [], "auth_env": [], "_exe": exe}}


def _rmtree(path: Path) -> None:
    def fix(func: Any, p: str, _exc: Any) -> None:   # Windows: git makes read-only object files
        try:
            os.chmod(p, stat.S_IWRITE)
            func(p)
        except OSError:
            pass
    if sys.version_info >= (3, 12):
        shutil.rmtree(path, onexc=fix)
    else:
        shutil.rmtree(path, onerror=fix)


def _git(cwd: Path, *args: str) -> int:
    r = subprocess.run(["git", "-c", "user.name=Orion Test", "-c", "user.email=test@localhost",
                        "-c", "commit.gpgsign=false", "-c", "core.hooksPath=", "-C", str(cwd), *args],
                       capture_output=True, text=True, timeout=60)
    return r.returncode


async def _dry_mission(tc: "TestCenter") -> tuple:
    import freeze
    if freeze.is_frozen():
        return "skip", "Orion is frozen, so no agent can start.", "Unfreeze Orion from your phone, then tap Run again."
    if not shutil.which("git"):
        return "fail", "Git isn't installed, and missions need it.", "Install Git, then tap Run again."
    import missions
    tmp = Path(tempfile.mkdtemp(prefix="orion-testcenter-"))
    mc = None
    try:
        repo = tmp / "scratch-repo"
        repo.mkdir()
        (repo / "README.md").write_text("Scratch repo for Orion's Test Center. Safe to delete.\n", encoding="utf-8")
        if await asyncio.to_thread(_git, repo, "init", "-q") != 0 or \
                await asyncio.to_thread(_git, repo, "add", "-A") != 0 or \
                await asyncio.to_thread(_git, repo, "commit", "-q", "-m", "start") != 0:
            return "fail", "Couldn't make a scratch git repo.", "Check that Git works, then tap Run again."
        routing = tmp / "routing.json"
        routing.write_text(json.dumps({"version": 1, "tasks": {},
                                       "default": {"agent": "fake", "fallback": [], "cost_cap_usd": 1.0,
                                                   "time_limit_min": 2},
                                       "review": {"agents": ["fake"], "time_limit_min": 2}}), encoding="utf-8")
        from approval_queue import ApprovalQueue
        queue = ApprovalQueue(tmp / "approvals.json")
        adapters = _fake_adapters()

        class Scratch(missions.MissionControl):
            def worktrees_dir(self) -> Path:   # never the shared agent folder
                return self.dir / "worktrees"

        mc = Scratch(tmp / "data", approvals=queue, adapters=adapters, routing_file=routing,
                     which=lambda b: adapters["fake"]["_exe"] if b == FAKE_BIN else None, review_timeout_s=60)
        mc.start()
        m = mc.create("Test Center dry run: add ORION_TEST.txt", repo=str(repo), source="test-center")
        deadline = time.monotonic() + MISSION_TIMEOUT_S
        rec = mc.get(m["id"]) or {}
        while time.monotonic() < deadline:
            rec = mc.get(m["id"]) or {}
            if rec.get("state") not in missions.ACTIVE and (rec.get("card_id") or rec.get("state") != "review"):
                break
            await asyncio.sleep(0.2)
        card = queue.get(rec["card_id"]) if rec.get("card_id") else None
        if rec.get("state") == "review" and card is not None:
            files = len(rec.get("changed_files") or [])
            return "pass", (f"A stand-in agent changed {files} file in a scratch repo, the work was checked, and a "
                            "review card was made. Nothing was approved, and the scratch repo is deleted.")
        why = str(rec.get("summary") or "it didn't finish in time")[:200]
        return "fail", f"The dry-run mission didn't reach a review card: {why}", \
            "Copy the report and share it with whoever fixes Orion."
    finally:
        if mc is not None:
            try:
                await mc.stop()
            except Exception:
                log.debug("scratch mission control stop failed", exc_info=True)
        await asyncio.to_thread(_rmtree, tmp)


async def _real_mission(tc: "TestCenter") -> tuple:
    """Not a check: says whether the real-mission item applies (it stays manual)."""
    return ("todo", "Claude Code is installed.") if tc.which("claude") else \
        ("skip", "Claude Code isn't installed, so there's no real mission to try.")


# =====================================================================================  manual helpers
async def _say_something(tc: "TestCenter") -> tuple:
    d = tc.daemon
    line = "This is Orion. If you can hear me, tap Yes."
    fn = getattr(d, "speak_and_stream", None)
    if fn is None:
        return "todo", "Orion's voice isn't available here."
    try:
        await asyncio.wait_for(fn(line), 30)
    except Exception as exc:
        return "todo", f"Orion couldn't speak: {type(exc).__name__}."
    return "todo", "Orion just spoke. Did you hear it?", {"ask": True}


def register_test_card(board: Any) -> None:
    import boardroom
    from boardroom import TaskType
    # sensitive -> the autonomy learner can never make it run by itself: it always waits for you
    boardroom.TASK_TYPES.setdefault(TEST_CARD_TASK, TaskType(TEST_CARD_TASK, "Test card (does nothing)", "system",
                                                             reversible=True, money=False, sensitive=True))
    if board is None:
        return
    if hasattr(board, "executors"):
        board.executors[TEST_CARD_TASK] = lambda d: {"test": True, "note": "Test card: nothing was done."}
    try:
        board.store.x("INSERT OR IGNORE INTO autonomy(task_type) VALUES(?)", (TEST_CARD_TASK,))
    except Exception:
        pass


async def _send_test_card(tc: "TestCenter") -> tuple:
    board = getattr(tc.daemon, "board", None)
    if board is None:
        return "todo", "Orion is still starting. Try again in a moment."
    if not _phones(tc):
        return "todo", "Pair your phone first (in the Phone group)."
    register_test_card(board)
    dec = await board.propose(TEST_CARD_TASK, "Test card from the Test Center. Approving it does nothing.",
                              {"what": "Checks that approvals reach your phone and need your fingerprint."},
                              proposer="Test Center", risk="low", simulated=False)
    tc.state["test_card"] = dec.get("id")
    tc.save()
    return "todo", "Sent. Your phone shows a test card now: approve it with your fingerprint."


def _passes_only(check: Check) -> Callable[["TestCenter"], Awaitable[Optional[tuple]]]:
    """A watcher from an automatic check: it only ever ticks the item off (a failure is left for Run to show)."""
    async def watch(tc: "TestCenter") -> Optional[tuple]:
        res = await check(tc)
        return ("pass", res[1]) if res and res[0] == "pass" else None
    return watch


def _open_hud(what: str) -> Check:
    async def run(tc: "TestCenter") -> tuple:
        await tc.publish({"event": HUD_OPEN_EVENT, "what": what})
        return "todo", "Opened it in the Orion window."
    return run


# =====================================================================================  the checklist
def build_items() -> list[Item]:
    I = Item
    A = auto
    PHONE_WATCH = "Ticks itself when your phone runs its own test (Orion Mobile > Menu > Test)."
    return [
        # ---------------------------------------------------------------- Start-up
        I("daemon_up", "startup", "Orion's brain is running", "pc", "auto",
          "Checks that Orion's brain answers.", "Tap Run all automatic checks.", check=_daemon_up),
        I("key_works", "startup", "Orion's key works", "pc", "auto",
          "Checks that local tools can read Orion's key and that Orion accepts it.", "Tap Run all automatic checks.",
          check=_key_works),
        I("hud_connected", "startup", "The Orion window is connected", "pc", "auto",
          "Checks that this window talks to Orion's brain.", "Tap Run all automatic checks.", check=_hud_connected,
          detect=_passes_only(_hud_connected), watch="Ticks itself when the Orion window connects."),
        I("health_light", "startup", "Health light is green", "pc", "auto",
          "Checks Orion's health light (green or amber is fine).", "Tap Run all automatic checks.",
          check=_health_light),
        I("parts_ok", "startup", "Every part started", "pc", "auto",
          "Checks that no part of Orion was switched off because it failed.", "Tap Run all automatic checks.",
          check=_parts_ok),
        # ---------------------------------------------------------------- Look
        I("core_renders", "look", "The black hole draws and picks a quality", "pc", "auto",
          "The Orion window checks that the black hole can draw and which quality it picked.",
          "Tap Do it for me.", hud=True),
        I("glass_windows", "look", "Windows look like frosted glass", "pc", "manual",
          "Orion's windows should be see-through glass with the black hole glowing behind them.",
          "Tap Do it for me, look at this window, and answer the question.",
          action=("Do it for me", A.ask_only("Look at this window. Can you see the black hole glowing through it?"))),
        # ---------------------------------------------------------------- Voice
        I("mic_found", "voice", "Orion hears a microphone", "pc", "auto",
          "Checks that Orion's voice typing is running with a microphone.", "Tap Run all automatic checks.",
          check=_mic_found),
        I("tts_speaks", "voice", "Orion speaks out loud", "pc", "manual",
          "Orion says one sentence out of your speakers.",
          "Turn your sound up and tap Do it for me. Orion speaks, then asks if you heard it.",
          action=("Say something", _say_something)),
        I("mic_off_default", "voice", "Hot mic and wake word are off by default", "pc", "auto",
          "Checks that Orion doesn't listen all the time unless you turn it on.", "Tap Run all automatic checks.",
          check=_mic_off_default),
        # ---------------------------------------------------------------- Phone
        I("phone_paired", "phone", "Your phone is paired", "pc", "auto",
          "Checks that a phone is paired with this PC.", "Tap Run all automatic checks.", check=_phone_paired,
          action=("Open pairing", _open_hud("phone")), detect=_passes_only(_phone_paired),
          watch="Ticks itself as soon as your phone is paired."),
        I("phone_reaches", "phone", "Your phone reaches this PC", "both", "auto",
          "Checks that your phone talked to this PC in the last 15 minutes.",
          "Open Orion Mobile on your phone, then tap Run all automatic checks.", check=_phone_reaches,
          detect=_passes_only(_phone_reaches), watch="Ticks itself when your phone talks to this PC."),
        I("phone_fingerprint_key", "phone", "The phone's fingerprint key is ready", "phone", "manual",
          "Your phone checks this itself: the approval key is in its secure chip and needs your fingerprint.",
          "On your phone, open Orion Mobile > Menu > Test. It fills this in by itself.", watch=PHONE_WATCH,
          by_phone=True),
        I("phone_notifications", "phone", "The phone can read message notifications", "phone", "manual",
          "Needed for the one inbox. Your phone checks this itself.",
          "On your phone, open Orion Mobile > Menu > Test and tap Do it for me: it opens the right page.",
          watch=PHONE_WATCH, by_phone=True),
        I("phone_assistant", "phone", "Long-press power opens Orion", "phone", "manual",
          "Orion is the phone's assistant app. Your phone checks this itself.",
          "On your phone, open Orion Mobile > Menu > Test and tap Do it for me: it opens the right page.",
          watch=PHONE_WATCH, by_phone=True),
        I("phone_battery", "phone", "The phone won't put Orion to sleep", "phone", "manual",
          "Battery saving is off for Orion, so alerts arrive on time. Your phone checks this itself.",
          "On your phone, open Orion Mobile > Menu > Test and tap Do it for me: it opens the right page.",
          watch=PHONE_WATCH, by_phone=True),
        I("approve_card", "phone", "Approve a test card with your fingerprint", "both", "manual",
          "Orion sends a test card that does nothing. You approve it on your phone with your fingerprint.",
          "Tap Do it for me, then approve the card on your phone with your fingerprint.",
          action=("Send a test card", _send_test_card),
          watch="Ticks itself when you approve (or reject) the card on your phone. Orion never approves it."),
        I("inbox_draft", "phone", "Drafts are never sent by the PC", "pc", "auto",
          "Makes a test reply draft and checks that nothing is sent.", "Tap Run all automatic checks.",
          check=_inbox_draft),
        I("phone_draft", "phone", "Draft a reply on your phone", "phone", "manual",
          "On the phone, a draft appears in your style and nothing is sent until you tap Send yourself.",
          "On your phone, open a message in Orion's inbox, tap Draft, then close it without sending.",
          detect=A.watch_phone_draft, watch="Ticks itself when your phone asks this PC for a draft."),
        I("send_work", "phone", "Send work to the PC", "phone", "manual",
          "From the phone, queue a small mission for the PC's agents.",
          "On your phone, open Missions and send \"Test mission\" to your PC.",
          watch="Ticks itself when a mission from your phone reaches this PC."),
        I("pocket_orion", "phone", "Pocket Orion answers when the PC is off", "phone", "manual",
          "Only if you set up Pocket Orion: the phone keeps working without the PC.",
          "Quit Orion on the PC, ask Orion Mobile something, and tap Done if Pocket Orion answers (your phone "
          "skips this by itself if Pocket Orion isn't set up)."),
        # ---------------------------------------------------------------- Agents
        I("agents_found", "agents", "Coding agents are found", "pc", "auto",
          "Looks for Claude Code, Codex and Gemini CLI on this PC.", "Tap Run all automatic checks.",
          check=_agents_found),
        I("dry_mission", "agents", "A practice mission makes a review card", "pc", "auto",
          "A stand-in agent edits a scratch repo in a temporary folder; Orion checks it and makes a review card. "
          "Nothing is approved and everything is deleted afterwards.",
          "Tap Run all automatic checks.", check=_dry_mission),
        I("real_mission", "agents", "A real mission with Claude Code", "pc", "manual",
          "A small real mission with Claude Code on a test repo, from start to review card. It may cost a few "
          "cents. Merging stays your choice.",
          "Tap Do it for me to start \"Add a hello file\" with Claude Code on a test repo.", open="missions",
          action=("Start it (a few cents)", A.start_hello_mission), detect=A.watch_hello_mission,
          watch="Ticks itself when the review card arrives."),
        # ---------------------------------------------------------------- Projects
        I("start_project", "projects", "Start a project with Claude", "pc", "manual",
          "Starting a project makes the project and its first mission.",
          "Tap Do it for me: Projects opens with a sample idea filled in. Press Start there.",
          open="projects", action=("Open with a sample idea", A.open_project_wizard), detect=A.watch_project_review,
          watch="Ticks itself when the project's first mission reaches review."),
        # ---------------------------------------------------------------- Skills
        I("record_skill", "skills", "Record a tiny skill", "pc", "manual",
          "Orion watches you do 3 small steps once on a practice page that only lives on this computer.",
          "Tap Do it for me: a practice page opens and Orion starts recording. Do its 3 steps, then press Save "
          "on the skill card.", open="skills", action=("Record a practice skill", A.record_practice_skill),
          detect=A.watch_skill_saved, watch="Ticks itself when the skill is saved."),
        I("replay_skill", "skills", "Replay the skill", "pc", "manual",
          "Orion repeats what you recorded.",
          "Tap Do it for me: Orion replays the practice skill on the practice page.", open="skills",
          action=("Replay it", A.replay_practice_skill), detect=A.watch_skill_replayed,
          watch="Ticks itself when the replay gets to the end."),
        # ---------------------------------------------------------------- Safety
        I("freeze_phone", "safety", "Freeze Orion from your phone", "both", "manual",
          "The kill switch: everything that changes Orion stops.",
          "On your phone, tap Freeze and confirm with your fingerprint.",
          watch="Ticks itself when Orion is frozen from your phone."),
        I("unfreeze_phone", "safety", "Unfreeze from your phone", "both", "manual",
          "Only your fingerprint lets Orion run again.",
          "On your phone, tap Unfreeze and approve it with your fingerprint.",
          watch="Ticks itself when Orion runs again after your approval."),
        I("undo_last", "safety", "Undo the last change", "pc", "manual",
          "Orion can always go back to how it was before its last change.",
          "After the screen change test, approve the practice change if you like, then tap Do it for me to put it "
          "back.", action=("Undo it", A.undo_practice_or_open), detect=A.watch_undo,
          watch="Ticks itself when a change is undone."),
        I("privacy_off", "safety", "The privacy dashboard turns everything off", "pc", "manual",
          "One button turns off everything Orion can see or hear.",
          "Tap Do it for me: Orion presses Turn everything off and reads every switch back.", open="privacy",
          action=("Turn everything off", A.privacy_off_all)),
        # ---------------------------------------------------------------- Self-change
        I("ghost_preview", "selfchange", "A small screen change shows a preview first", "pc", "manual",
          "Orion builds a practice change (it only recolours a practice banner) in a separate copy and shows a "
          "ghost preview before anything changes. The Test Center never applies it.",
          "Tap Do it for me and wait for the ghost preview in the corner (a minute or two).",
          action=("Make a practice change", A.stage_practice_change), detect=A.watch_ghost_ready,
          watch="Ticks itself when the ghost preview is ready."),
        # ---------------------------------------------------------------- OrionOS only
        I("os_usb_boot", "orionos", "The USB stick starts the computer", "pc", "manual",
          "Only on real hardware: OrionOS starts from the USB stick.",
          "Restart with the Orion USB stick plugged in, pick it in the boot menu, and tap Done when Orion appears.",
          only=_on_stick, detect=A.watch_usb_boot, watch="Ticks itself when Orion runs from the stick."),
        I("os_vault", "orionos", "Your vault opens with your passphrase", "pc", "manual",
          "Your files on the stick are locked until you type your passphrase.",
          "Restart, type your vault passphrase when asked, and tap Done when your files are there.", only=_on_stick,
          detect=A.watch_vault, watch="Ticks itself when your vault is open."),
        I("os_wifi", "orionos", "Wi-Fi is connected", "pc", "auto",
          "Checks that the computer is on a network.", "Tap Run all automatic checks.", check=_wifi, only=_on_os),
        I("os_sleep_lock", "orionos", "Sleep and lock work", "pc", "manual",
          "Only on real hardware: closing the lid sleeps and locks; opening it asks for your password.",
          "Tap Do it for me, close the lid for ten seconds, and open it.", only=_on_os,
          action=("Start", A.arm_sleep), detect=A.watch_sleep,
          watch="Notices the sleep by itself, then asks if it locked."),
        I("os_restart_windows", "orionos", "Restart into Windows", "pc", "manual",
          "Windows starts once; next time OrionOS is the normal choice again.",
          "Open the app menu, pick Restart into Windows, and tap Done when you're back in Orion.",
          only=_has_windows_too),
        # ---------------------------------------------------------------- Windows only
        I("win_shortcut", "windows", "The desktop shortcut starts Orion", "pc", "manual",
          "Start Orion.cmd puts an Orion shortcut on your desktop.",
          "Tap Do it for me, close Orion, double-click the Orion shortcut, and answer the question.",
          only=_on_windows, action=("Find the shortcut", A.check_shortcut), detect=A.watch_shortcut),
        I("win_phone_access", "windows", "Your phone can reach this PC on Wi-Fi", "pc", "manual",
          "Lets phones on your home Wi-Fi reach Orion's phone port, nothing else.",
          "Tap Do it for me. If Windows asks for permission, say yes.",
          only=_on_windows, action=("Let my phone in", A.let_phone_in), detect=A.watch_phone_access,
          watch="Ticks itself when the firewall lets your phone in."),
        I("win_ready", "windows", "This PC is ready for OrionOS", "pc", "manual",
          "Checks the PC before you try OrionOS on it. It changes nothing.",
          "Tap Do it for me: the readiness check runs in its own window.", only=_on_windows,
          action=("Run the readiness check", A.run_ready_check), detect=A.watch_ready_report,
          watch="Ticks itself when the readiness report is saved."),
    ]


# =====================================================================================  the Test Center
class TestCenter:
    __test__ = False   # not a pytest class

    def __init__(self, daemon: Any, data_dir: Path, *, platform: Optional[str] = None,
                 profile: Optional[str] = None, which: Callable[[str], Optional[str]] = shutil.which,
                 clock: Callable[[], float] = time.time) -> None:
        self.daemon = daemon
        self.path = Path(data_dir) / "test_center.json"
        self.platform = platform or detect_platform()
        self.profile = os_profile() if profile is None else profile
        self.which = which
        self.clock = clock
        self.version = ""
        self.all_items = build_items()
        self.by_id = {i.id: i for i in self.all_items}
        self._lock = threading.RLock()
        self._task: Optional[asyncio.Task] = None
        self._bg: set[asyncio.Task] = set()
        self._last_detect: dict[str, float] = {}
        self._detecting = False
        self._practice: Any = None                         # the practice skill page (test_center_auto.py)
        self.browser: Optional[Callable[[str], Any]] = None   # tests replace how pages are opened
        self.popen: Optional[Callable[..., Any]] = None
        self.state: dict[str, Any] = {"results": {}, "started": self.clock()}
        self._load()

    # ------------------------------------------------------------ storage
    def _load(self) -> None:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(raw, dict) and isinstance(raw.get("results"), dict):
                self.state = raw
        except (OSError, ValueError):
            pass
        # a check that was running when Orion stopped never finished
        for r in self.state["results"].values():
            if r.get("status") == "running":
                r["status"] = "todo"

    def save(self) -> None:
        with self._lock:
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                tmp = self.path.with_suffix(".tmp")
                tmp.write_text(json.dumps(self.state, indent=1), encoding="utf-8")
                os.replace(tmp, self.path)
            except OSError as exc:
                log.warning("Couldn't save the Test Center results: %s", exc)

    # ------------------------------------------------------------ items
    def items(self) -> list[Item]:
        return [i for i in self.all_items if i.only is None or i.only(self)]

    def _result(self, iid: str) -> dict[str, Any]:
        return self.state["results"].get(iid) or {}

    def _set(self, iid: str, status: str, detail: Optional[str] = None, *, note: Optional[str] = None,
             next_: Optional[str] = None, by: str = "orion", facts: Optional[dict[str, Any]] = None,
             ask: Optional[str] = None, armed: Optional[float] = None) -> None:
        with self._lock:
            r = dict(self._result(iid))
            r["status"] = status
            if ask:
                r["ask"] = str(ask)[:200]
            elif status != "todo" or armed is not None or by == "you":
                r.pop("ask", None)                       # a new result or a fresh action ends the question
            if armed is not None:
                r["armed"] = int(armed * 10) / 10.0            # rounded down: never later than it was
            elif status in ("pass", "fail", "skip"):
                r.pop("armed", None)
            if detail is not None:
                r["detail"] = str(detail)[:400]
            if note is not None:
                r["note"] = _clean(note)[:NOTE_MAX]
            if next_ is not None or status in ("pass", "skip", "todo"):
                r["next"] = next_ or ""
            if facts:
                r["facts"] = facts
            r["by"], r["at"] = by, round(self.clock(), 1)
            self.state["results"][iid] = r

    def public(self, it: Item) -> dict[str, Any]:
        r = self._result(it.id)
        status = r.get("status") or "todo"
        if status == "running":
            nxt = "Wait a moment while Orion checks this."
        elif status in ("pass", "skip"):
            nxt = "Nothing more to do here."
        elif status == "fail":
            nxt = r.get("next") or it.fix or ("Tap Run to try again." if it.kind == "auto" else
                                              "Try it again, then tap Done or It didn't work.")
        else:
            nxt = r.get("next") or it.next
        ask = r.get("ask") if status == "todo" else None
        if ask:
            nxt = ask
        elif status == "todo" and r.get("armed") and it.watch:
            nxt = f"{r.get('detail') or it.next} {it.watch}".strip()
        return {"id": it.id, "group": it.group, "title": it.title, "where": it.where, "kind": it.kind,
                "status": status, "detail": r.get("detail") or it.detail, "next": nxt,
                "action": it.action[0] if it.action else None, "open": it.open, "note": r.get("note", ""),
                "at": r.get("at"), "do": self._do_label(it, status), "ask": ask or None, "watch": it.watch,
                "armed": bool(r.get("armed")) and status == "todo", "by_phone": it.by_phone}

    @staticmethod
    def _do_label(it: Item, status: str) -> Optional[str]:
        """The "Do it for me" button: what it does, in a few words (None: nothing the PC can do for you)."""
        if status in ("pass", "skip", "running"):
            return None
        if it.hud:
            return "Check it"
        if it.check is not None:
            return it.action[0] if (it.action and status == "fail") else "Run the check"
        return it.action[0] if it.action else None

    # ------------------------------------------------------------ "seen it happen" for manual items
    def sniff(self) -> None:
        """Mark a manual item passed when Orion can see it really happened (never fails anything)."""
        changed = False
        board = getattr(self.daemon, "board", None)
        card = self.state.get("test_card")
        if card and board is not None and self._result("approve_card").get("status") in (None, "todo"):
            try:
                d = board.get(card)
            except Exception:
                d = None
            if d is not None and d.get("status") not in ("pending", None):
                word = "approved" if d["status"] in ("approved", "edited") else d["status"]
                self._set("approve_card", "pass", f"The test card was {word} on your phone with your fingerprint.",
                          by="seen")
                changed = True
        try:
            import freeze
            st = freeze.state()
        except Exception:
            st = {}
        by_phone = str(st.get("by") or "").startswith("phone")
        if st.get("frozen") and by_phone and self._result("freeze_phone").get("status") in (None, "todo"):
            self._set("freeze_phone", "pass", "Orion was frozen from your phone.", by="seen")
            self.state["saw_frozen"] = True
            changed = True
        if not st.get("frozen") and self.state.get("saw_frozen") and \
                self._result("unfreeze_phone").get("status") in (None, "todo"):
            self._set("unfreeze_phone", "pass", "Orion was unfrozen after your approval.", by="seen")
            self.state.pop("saw_frozen", None)
            changed = True
        mc = getattr(self.daemon, "mission_control", None)
        if mc is not None and self._result("send_work").get("status") in (None, "todo"):
            since = float(self.state.get("started") or 0)
            try:
                seen = any(m.get("source") == "phone" and float(m.get("created") or 0) >= since for m in mc.list())
            except Exception:
                seen = False
            if seen:
                self._set("send_work", "pass", "A mission from your phone reached the PC.", by="seen")
                changed = True
        if changed:
            self.save()

    # ------------------------------------------------------------ watchers: tick items off when Orion sees them happen
    async def refresh(self) -> None:
        """Run the watchers of items still to do (each at most every DETECT_EVERY_S). Never approves anything."""
        self.sniff()
        if self._detecting:
            return
        self._detecting = True
        changed = False
        try:
            now = self.clock()
            for it in self.items():
                if it.detect is None:
                    continue
                r = self._result(it.id)
                st = r.get("status") or "todo"
                if st not in ("todo", "fail") or r.get("ask"):
                    continue
                if now - self._last_detect.get(it.id, 0.0) < DETECT_EVERY_S:
                    continue
                self._last_detect[it.id] = now
                try:
                    res = await asyncio.wait_for(it.detect(self), DETECT_TIMEOUT_S)
                except Exception:
                    log.debug("Test Center watcher %s failed", it.id, exc_info=True)
                    continue
                if not res:
                    continue
                kind, words = res[0], str(res[1] if len(res) > 1 else "")
                if kind == "pass":
                    self._set(it.id, "pass", words, by="seen")
                elif kind == "ask" and st == "todo":
                    self._set(it.id, "todo", words, ask=words, by="seen")
                elif kind == "fail" and st == "todo" and r.get("armed"):   # only the result of our own action
                    self._set(it.id, "fail", words, by="seen")
                else:
                    continue
                changed = True
        finally:
            self._detecting = False
        if changed:
            self.save()

    async def view_fresh(self, where: Optional[str] = None) -> dict[str, Any]:
        await self.refresh()
        return self.view(where)

    # ------------------------------------------------------------ the view
    def view(self, where: Optional[str] = None) -> dict[str, Any]:
        self.sniff()
        wanted = (lambda it: it.where in ("phone", "both")) if where == "phone" else (lambda it: True)
        groups, flat = [], []
        for gid, title in GROUPS:
            items = [self.public(it) for it in self.items() if it.group == gid and wanted(it)]
            if items:
                groups.append({"id": gid, "title": title, "items": items})
                flat += items
        summary = {"pass": 0, "fail": 0, "todo": 0, "skip": 0}
        for x in flat:
            summary["todo" if x["status"] in ("todo", "running") else x["status"]] += 1
        todo = [x for x in flat if x["status"] == "todo"]
        never_run = [x for x in todo if x["kind"] == "auto" and not x.get("at")]
        nxt = next(iter(never_run), None) or next(iter(todo), None)
        return {"groups": groups, "next": nxt, "summary": summary, "running": self.running(),
                "platform": self.platform, "all_done": bool(flat) and summary["todo"] == 0 and summary["fail"] == 0,
                "last_run": self.state.get("last_run")}

    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    # ------------------------------------------------------------ running checks
    async def _run_check(self, it: Item) -> None:
        if it.check is None:
            return
        self._set(it.id, "running")
        timeout = MISSION_TIMEOUT_S + 30 if it.id == "dry_mission" else CHECK_TIMEOUT_S
        try:
            res = await asyncio.wait_for(it.check(self), timeout)
        except asyncio.TimeoutError:
            res = ("fail", "The check took too long.", "Tap Run to try again.")
        except Exception as exc:
            log.exception("Test Center check %s failed", it.id)
            res = ("fail", f"The check itself broke: {type(exc).__name__}.", "Copy the report and share it.")
        status, detail = res[0], res[1]
        self._set(it.id, status, detail, next_=res[2] if len(res) > 2 else None)
        self.save()

    def _auto_items(self) -> list[Item]:
        return [it for it in self.items() if it.kind == "auto" and it.check is not None]

    async def _run_all(self, items: list[Item]) -> None:
        for it in items:
            await self._run_check(it)
        # manual items that can't apply here say so straight away
        if "real_mission" in self.by_id and self._result("real_mission").get("status") in (None, "todo"):
            st, detail = await _real_mission(self)
            if st == "skip":
                self._set("real_mission", "skip", detail)
        hud = self.by_id.get("core_renders")
        if hud is not None and self._result("core_renders").get("status") in (None, "todo"):
            self._set("core_renders", "todo", "The Orion window checks this when you press Run all there.")
        self.state["last_run"] = round(self.clock(), 1)
        self.save()
        self._last_detect.clear()
        await self.refresh()

    def run_auto(self, fresh: bool = True) -> dict[str, Any]:
        """Every automatic check (fresh=False: only the ones that haven't passed yet, used when the window opens)."""
        if not self.running():
            items = [it for it in self._auto_items()
                     if fresh or self._result(it.id).get("status") not in ("pass", "skip")]
            for it in items:
                self._set(it.id, "running")
            self.save()
            self._task = asyncio.get_running_loop().create_task(self._run_all(items), name="test-center")
        return self.view()

    async def run_one(self, iid: str) -> dict[str, Any]:
        it = self.by_id.get(iid)
        if it is None or it not in self.items():
            raise KeyError(iid)
        status_now = self._result(iid).get("status") or "todo"
        use_action = it.action is not None and (it.check is None or status_now == "fail")
        if use_action:
            t0 = self.clock()                            # watch from before the action, not after it
            try:
                res = await asyncio.wait_for(it.action[1](self), 40)
            except Exception as exc:
                log.debug("Test Center action %s failed", iid, exc_info=True)
                res = ("todo", f"That didn't work: {type(exc).__name__}.")
            status, detail = res[0], res[1]
            extra = res[2] if len(res) > 2 and isinstance(res[2], dict) else {}
            if status == "todo":
                self._set(iid, "todo", detail, ask=detail if extra.get("ask") else None, armed=t0)
            else:
                self._set(iid, status, detail)
            self._last_detect.pop(iid, None)
            self.save()
        elif it.check is not None:
            if status_now != "running":
                self._set(iid, "running")
                t = asyncio.get_running_loop().create_task(self._run_check(it))
                self._bg.add(t)
                t.add_done_callback(self._bg.discard)
        return self.public(it)

    def set_result(self, iid: str, status: str, note: Optional[str] = None, *, by: str = "you",
                   facts: Optional[dict[str, Any]] = None) -> dict[str, Any]:
        it = self.by_id.get(iid)
        if it is None:
            raise KeyError(iid)
        if status not in SET_STATUSES:
            raise ValueError("status must be pass, fail, skip or todo")
        if by in ("orion-window", "phone-check", "phone") and note:
            detail: Optional[str] = _clean(note)[:200]     # what the window or the phone measured
            note = None
        else:
            detail = {"pass": "You said it works.", "fail": "You said it didn't work.", "skip": "Skipped.",
                      "todo": ""}[status]
        self._set(iid, status, detail, note=note, next_="", by=by, facts=_clean_facts(facts))
        if status == "todo":
            self.state["results"][iid].pop("armed", None)
        self.save()
        return self.public(it)

    def reset(self) -> dict[str, Any]:
        with self._lock:
            self.state = {"results": {}, "started": round(self.clock(), 1)}
        self.save()
        return self.view()

    # ------------------------------------------------------------ opening the window
    async def publish(self, event: dict[str, Any]) -> None:
        bus = getattr(self.daemon, "bus", None)
        if bus is not None:
            try:
                await bus.publish(event, record=False)
            except Exception:
                log.debug("Test Center publish failed", exc_info=True)

    async def request_open(self, run: bool = False) -> dict[str, Any]:
        """Ask the Orion window to open the Test Center (now if it's connected, else when it connects)."""
        if run:
            self.run_auto()
        self.state["open_request"] = round(self.clock(), 1)
        self.state["open_run"] = bool(run)
        self.save()
        await self.publish({"event": HUD_OPEN_EVENT, "what": "tests", "run": bool(run)})
        return {"ok": True}

    def take_open_request(self) -> dict[str, bool]:
        """{"open", "run"} once; a request older than 15 minutes is forgotten."""
        ts = float(self.state.pop("open_request", 0) or 0)
        run = bool(self.state.pop("open_run", False))
        if ts:
            self.save()
        fresh = bool(ts) and self.clock() - ts <= OPEN_TTL_S
        return {"open": fresh, "run": fresh and run}

    # ------------------------------------------------------------ the report
    def report(self) -> str:
        v = self.view()
        s = v["summary"]
        where = {"windows": "Windows", "orionos": "OrionOS", "other": _platform.system() or "this computer"}
        lines = ["# Orion test report", "",
                 f"{time.strftime('%Y-%m-%d', time.gmtime(self.clock()))} · {where.get(self.platform, 'PC')}"
                 + (f" · Orion {self.version}" if self.version else ""), "",
                 f"**{s['pass']} passed, {s['fail']} didn't work, {s['todo']} still to do, {s['skip']} skipped.**", ""]
        mark = {"pass": "[x]", "fail": "[!]", "skip": "[-]", "todo": "[ ]", "running": "[ ]"}
        for g in v["groups"]:
            lines.append(f"## {g['title']}")
            for x in g["items"]:
                line = f"- {mark[x['status']]} {x['title']}"
                if x["status"] in ("fail", "skip") or x["id"] in ("core_renders", "agents_found"):
                    extra = self._scrub(x["detail"])
                    if x.get("note"):
                        extra += f" Note: {self._scrub(x['note'])}"
                    if extra.strip() and x["status"] != "todo":
                        line += f": {extra.strip()}"
                lines.append(line)
            lines.append("")
        lines.append("Key: [x] works, [!] didn't work, [ ] not checked yet, [-] skipped.")
        return "\n".join(lines).strip() + "\n"

    def _scrub(self, text: str) -> str:
        """No keys and no names: the phone names, this computer's name and the user name are taken out."""
        out = _clean(text)
        names = {p.get("name", "") for p in _phones(self)}
        try:
            names.add(socket.gethostname())
        except Exception:
            pass
        try:
            names.add(getpass.getuser())
        except Exception:
            pass
        names.add(Path.home().name)
        # Generic account and machine names aren't personal, and OrionOS's own user is called "orion": hiding those
        # would turn "orion-ctl" into "[name]-ctl". Real names are whole words, so match whole words only.
        generic = {"orion", "orionos", "orion-pc", "user", "admin", "ubuntu", "root", "localhost", "guest"}
        for n in sorted((n for n in names if n and len(n) >= 3 and n.lower() not in generic), key=len, reverse=True):
            out = re.sub(r"(?<![\w-])" + re.escape(n) + r"(?![\w-])", "[name]", out, flags=re.I)
        out = re.sub(r"([A-Za-z]:)?[\\/](Users|home)[\\/][^\\/\s]+", r"\1/\2/[name]", out)
        return out[:240]


# =====================================================================================  helpers
_SECRETISH = re.compile(r"\b(sk-[A-Za-z0-9_-]{8,}|gh[pousr]_[A-Za-z0-9]{12,}|AKIA[0-9A-Z]{12,}|xox[abpr]-[A-Za-z0-9-]{8,}|"
                        r"[A-Za-z0-9_-]{32,})\b")


def _clean(text: Any) -> str:
    out = " ".join(str(text or "").split())
    try:
        from mcp_tools import redact
        out = redact(out)
    except Exception:
        pass
    return _SECRETISH.sub("[hidden]", out)


def _clean_facts(facts: Any) -> Optional[dict[str, Any]]:
    """Only small plain values from the phone (e.g. Android version, app version)."""
    if not isinstance(facts, dict):
        return None
    out: dict[str, Any] = {}
    for k, v in list(facts.items())[:FACTS_MAX]:
        key = re.sub(r"[^a-z0-9_]", "", str(k).lower())[:32]
        if not key or any(w in key for w in ("key", "token", "secret", "pass", "name", "phone_number", "email")):
            continue
        if isinstance(v, bool) or isinstance(v, (int, float)):
            out[key] = v
        elif isinstance(v, str):
            out[key] = _clean(v)[:80]
    return out or None


# =====================================================================================  voice / chat
_WORDS = re.compile(r"^\W*(hey\s+)?(orion\W+)?(please\s+)?(test everything|test orion|run (all )?(the )?tests|"
                    r"open (the )?test cent(er|re))\W*$", re.I)


def matches(text: str) -> bool:
    return bool(_WORDS.match(text or ""))


async def chat(daemon: Any, text: str) -> Optional[str]:
    """"Orion, test everything": open the Test Center in the Orion window and start the automatic checks."""
    if not matches(text):
        return None
    await center(daemon).request_open(run=True)
    return "Opening the Test Center and running the automatic checks. Then follow the Next card."


# =====================================================================================  wiring
_centers: dict[int, TestCenter] = {}


def center(daemon: Any, data_dir: Optional[Path] = None) -> TestCenter:
    tc = getattr(daemon, "test_center", None)
    if isinstance(tc, TestCenter):
        return tc
    if data_dir is None:
        from config import settings
        data_dir = settings.data_dir
    tc = TestCenter(daemon, Path(data_dir))
    try:
        daemon.test_center = tc
    except Exception:
        _centers[id(daemon)] = tc
    return tc


def install(daemon: Any) -> None:
    """Register the do-nothing test card with the boardroom (approving it only proves the fingerprint works)."""
    register_test_card(getattr(daemon, "board", None))
    center(daemon)


def make_router(get_daemon: Callable[[], Any]):
    router = APIRouter()

    def tc() -> TestCenter:
        return center(get_daemon())

    async def body(request: Request) -> dict[str, Any]:
        try:
            data = await request.json()
        except Exception:
            return {}
        return data if isinstance(data, dict) else {}

    @router.get("/tests")
    async def tests_view() -> Any:
        return await tc().view_fresh()

    @router.post("/tests/run-auto")
    async def tests_run_auto(request: Request) -> Any:
        data = await body(request)
        return tc().run_auto(fresh=data.get("fresh", True) is not False)

    @router.post("/tests/reset")
    async def tests_reset() -> Any:
        return tc().reset()

    @router.get("/tests/report")
    async def tests_report() -> Any:
        return {"markdown": tc().report()}

    @router.post("/tests/open")
    async def tests_open(request: Request) -> Any:
        data = await body(request)
        return await tc().request_open(run=bool(data.get("run")))

    @router.get("/tests/open-request")
    async def tests_open_request() -> Any:
        return tc().take_open_request()

    @router.post("/tests/{item_id}/run")
    async def tests_run_one(item_id: str) -> Any:
        try:
            return {"item": await tc().run_one(item_id)}
        except KeyError:
            return JSONResponse({"error": "There's no test with that name here."}, status_code=404)

    @router.post("/tests/{item_id}/result")
    async def tests_result(item_id: str, request: Request) -> Any:
        data = await body(request)
        by = "orion-window" if data.get("by") == "orion-window" else "you"
        try:
            return {"item": tc().set_result(item_id, str(data.get("status") or ""), data.get("note"), by=by)}
        except KeyError:
            return JSONResponse({"error": "There's no test with that name here."}, status_code=404)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)

    return router
