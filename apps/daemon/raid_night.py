"""Raid Night Autopilot (Toolkit tool 24, docs/ORION_TOOLKIT_PLAN.md).

Set it once ("Orion, raid night is Fridays at 7"). Then, two hours before each raid (your local time zone):

  1. Server:  OpenWoW is up and healthy (the wow agent's checks).
  2. Backup:  last night's backup exists and restores (the backup tool's restore drill, when it's installed).
  3. Patch:   a staged patch (setting "patch_branch", or the newest local `patch/...` branch) passes Proof Gate.
  4. Post:    Orion drafts the Discord announcement. It says it was written with Orion's help. Posting it is one
              tap on your phone, "All good, post it" (boardroom decision `raid.announce`). It is never posted
              by itself.

During the raid Orion watches for crashes and lag spikes and keeps a short log. Restarting the server goes through
the usual approval (`wow.restart`, the wow agent's executor). Afterwards it drafts post-raid notes, which again only
go out if you approve them (`raid.notes`).

Settings: data_dir/raid_night.json (the Settings app's "Raid night" page and POST /raid/config write it).
"""
from __future__ import annotations

import asyncio
import inspect
import json
import logging
import re
import subprocess
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional

log = logging.getLogger("orion.raid")

TASK_ANNOUNCE = "raid.announce"
TASK_NOTES = "raid.notes"
DISCLOSURE = "(Written with Orion's help.)"
DAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
DEFAULTS: dict[str, Any] = {
    "enabled": False, "weekday": 4, "time": "19:00", "duration_minutes": 180, "lead_minutes": 120,
    "discord_channel_id": "", "project": "openwow", "patch_branch": "", "backup_target": "openwow",
    "lag_ms": 250, "name": "Raid night",
    "message": "{name} is tonight at {time}! {server_line}{patch_line}See you there.",
}
LOG_MAX = 200
TICK_S = 60.0
Check = Callable[[], Awaitable[tuple[Optional[bool], str]]]


# =====================================================================================  schedule
def parse_schedule(text: str) -> tuple[int, str]:
    """'Fridays at 7 pm' / 'friday 19:30' / 'every fri at 7' -> (weekday, 'HH:MM'). Bare hours mean evening."""
    t = (text or "").lower()
    day = None
    for i, d in enumerate(DAYS):
        if re.search(rf"\b{d[:3]}(?:{d[3:]})?s?\b", t):
            day = i
            break
    if day is None:
        raise ValueError("Say which day, like “Fridays at 7 pm”.")
    # "7:30" may arrive as "7 30" from a spoken or normalized phrase
    m = re.search(r"\b(\d{1,2})(?:[:. ](\d{2}))?\s*(am|pm|a\.m\.|p\.m\.|a m|p m)?(?!\w)",
                  t[t.find(DAYS[day][:3]) + 3:] or t)
    if not m:
        raise ValueError("Say what time, like “Fridays at 7 pm”.")
    h, mi, ampm = int(m.group(1)), int(m.group(2) or 0), (m.group(3) or "").replace(".", "").replace(" ", "")
    if ampm == "pm" and h < 12:
        h += 12
    elif ampm == "am" and h == 12:
        h = 0
    elif not ampm and 1 <= h <= 11:
        h += 12           # "raid at 7" is an evening raid
    if not (0 <= h <= 23 and 0 <= mi <= 59):
        raise ValueError("That time doesn't look right.")
    return day, f"{h:02d}:{mi:02d}"


def nice_time(hhmm: str) -> str:
    h, m = (int(x) for x in hhmm.split(":"))
    suffix = "am" if h < 12 else "pm"
    h12 = h % 12 or 12
    return f"{h12}{':%02d' % m if m else ''} {suffix}"


def describe_schedule(cfg: dict[str, Any]) -> str:
    return f"{DAYS[int(cfg['weekday'])].capitalize()}s at {nice_time(cfg['time'])}"


def raid_window(cfg: dict[str, Any], now: datetime) -> tuple[datetime, datetime]:
    """(start, end) of the raid that is running now or comes next, in local time."""
    h, m = (int(x) for x in cfg["time"].split(":"))
    dur = timedelta(minutes=int(cfg.get("duration_minutes") or 180))
    base = now.replace(hour=h, minute=m, second=0, microsecond=0)
    for back in range(0, 8):
        start = base - timedelta(days=back)
        if start.weekday() == int(cfg["weekday"]) and start <= now < start + dur + timedelta(hours=6):
            return start, start + dur
    for ahead in range(0, 8):
        start = base + timedelta(days=ahead)
        if start.weekday() == int(cfg["weekday"]) and start > now:
            return start, start + dur
    raise RuntimeError("no raid in the next week")   # unreachable


def phase(cfg: dict[str, Any], now: datetime) -> tuple[str, datetime, datetime]:
    start, end = raid_window(cfg, now)
    lead = timedelta(minutes=int(cfg.get("lead_minutes") or 120))
    if now < start - lead:
        return "idle", start, end
    if now < start:
        return "checklist", start, end
    if now < end:
        return "raid", start, end
    return "after", start, end


async def _maybe(v: Any) -> Any:
    return await v if inspect.isawaitable(v) else v


# =====================================================================================  default checks
async def _run(*args: str, timeout: float = 30) -> tuple[int, str]:
    try:
        proc = await asyncio.create_subprocess_exec(*args, stdout=asyncio.subprocess.PIPE,
                                                    stderr=asyncio.subprocess.STDOUT)
    except OSError as exc:
        return 127, str(exc)
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout)
    except asyncio.TimeoutError:
        proc.kill()
        return 124, "timed out"
    return proc.returncode or 0, out.decode(errors="replace")


def default_server_check(wow: Any) -> Check:
    async def check() -> tuple[Optional[bool], str]:
        if wow is None or not getattr(wow, "enabled", True):
            return None, "OpenWoW isn't set up on this computer, so the server wasn't checked."
        run = getattr(wow, "_run", None) or _run
        rc, out = await run("systemctl", "--user", "is-active", "openwow")
        if out.strip() != "active":
            _, status = await run(getattr(wow, "wow", "wow"), "status")
            err = next((l[12:] for l in status.splitlines() if l.startswith("Last error:")), "")
            return False, "The OpenWoW server isn't running" + (f" (last error: {err[:120]})" if err else "") + "."
        _, status = await run(getattr(wow, "wow", "wow"), "status")
        first = status.strip().splitlines()[0] if status.strip() else "running"
        return True, f"Server is up: {first[:120]}"
    return check


BACKUP_MODULES = ("backups", "backup_drills", "backup")


def default_backup_check(daemon: Any, target: Callable[[], str]) -> Check:
    """Uses the backup tool if it's installed (built separately); quietly skips when it isn't."""
    async def check() -> tuple[Optional[bool], str]:
        api = None
        for attr in ("backups", "backup", "backup_drills"):
            api = getattr(daemon, attr, None)
            if api is not None:
                break
        if api is None:
            import importlib
            for name in BACKUP_MODULES:
                try:
                    api = importlib.import_module(name)
                    break
                except Exception:
                    continue
        if api is None:
            return None, "Backups aren't set up yet, so last night's backup wasn't checked."
        tgt = target()
        if callable(getattr(api, "status", None)) and not hasattr(api, "last_backup"):
            # Orion's own backup tool (backup.py): one status() with the last backup and the last restore drill
            try:
                st = await _maybe(api.status())
            except Exception as exc:
                return None, f"Couldn't check the backup ({type(exc).__name__})."
            if not st.get("configured"):
                return None, "Backups aren't set up yet, so last night's backup wasn't checked."
            last = st.get("last_backup") or {}
            if not last.get("at"):
                return False, "There's no backup yet."
            age_h = (time.time() - float(last["at"])) / 3600
            if age_h > 36:
                return False, f"The last backup is {int(age_h)} hours old."
            drill = st.get("last_drill") or {}
            if not drill:
                return True, "Last night's backup exists (it hasn't had a restore test yet)."
            if drill.get("ok"):
                return True, "Last night's backup exists, and the last restore test passed."
            return False, "Last night's backup exists, but the last restore test failed."
        try:
            last_fn = next((getattr(api, n) for n in ("last_backup", "latest", "last") if hasattr(api, n)), None)
            drill_fn = next((getattr(api, n) for n in ("verify_restore", "restore_check", "drill", "restore_drill")
                             if hasattr(api, n)), None)
            if last_fn is None:
                return None, "The backup tool is installed but doesn't say when it last ran."
            last = await _maybe(last_fn(tgt))
            if not last:
                return False, f"No backup of {tgt} was found."
            when = next((last[k] for k in ("at", "time", "created") if k in last), None) \
                if isinstance(last, dict) else None
            age_h = (time.time() - float(when)) / 3600 if isinstance(when, (int, float)) else None
            if age_h is not None and age_h > 36:
                return False, f"The last backup of {tgt} is {int(age_h)} hours old."
            if drill_fn is None:
                return True, f"Last backup of {tgt} exists (restore not tested: the backup tool has no drill)."
            res = await _maybe(drill_fn(tgt))
            ok = bool(res.get("ok")) if isinstance(res, dict) else bool(res)
            note = (res.get("message") or res.get("text") or "") if isinstance(res, dict) else ""
            return ok, (f"Last backup of {tgt} restores fine." if ok else
                        f"The last backup of {tgt} didn't restore. {note}".strip())
        except Exception as exc:
            return None, f"Couldn't check the backup ({type(exc).__name__})."
    return check


def newest_patch_branch(repo: Path) -> str:
    try:
        out = subprocess.run(["git", "for-each-ref", "--sort=-committerdate", "--format=%(refname:short)",
                              "refs/heads/patch/"], cwd=str(repo), capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.SubprocessError):
        return ""
    return out.stdout.strip().splitlines()[0] if out.returncode == 0 and out.stdout.strip() else ""


def default_patch_check(proof: Any, project: Callable[[], Optional[Path]],
                        branch: Callable[[], str]) -> Check:
    async def check() -> tuple[Optional[bool], str]:
        repo = project()
        if repo is None:
            return None, "Couldn't find the server project, so no patch was checked."
        b = branch() or await asyncio.to_thread(newest_patch_branch, repo)
        if not b:
            return None, "No patch staged."
        if proof is None:
            return None, f"Patch {b} is staged, but Proof Gate isn't available to test it."
        res = await proof.prove(target=b, repo=str(repo), claim="raid night: patch tests pass")
        ok = res.get("verdict") == "green"
        return ok, (f"Patch {b} is tested and ready." if ok else
                    f"Patch {b} isn't ready: {res.get('reason', 'tests failed')}")
    return check


# =====================================================================================  the autopilot
class RaidNight:
    def __init__(self, data_dir: Path, board: Any = None, *, server: Optional[Check] = None,
                 backup: Optional[Check] = None, patch: Optional[Check] = None,
                 poster: Optional[Callable[[str, str], Awaitable[bool]]] = None,
                 restart: Optional[Callable[[str], Awaitable[Any]]] = None,
                 lag_probe: Optional[Callable[[], Awaitable[Optional[float]]]] = None,
                 notify: Optional[Callable[[str, str], None]] = None,
                 clock: Callable[[], datetime] = lambda: datetime.now().astimezone()) -> None:
        Path(data_dir).mkdir(parents=True, exist_ok=True)
        self.path = Path(data_dir) / "raid_night.json"
        self.board = board
        self.server = server or (lambda: _none("Server check isn't set up."))
        self.backup = backup or (lambda: _none("Backups aren't set up yet, so last night's backup wasn't checked."))
        self.patch = patch or (lambda: _none("No patch staged."))
        self.poster = poster
        self.restart = restart
        self.lag_probe = lag_probe
        self.notify = notify or (lambda t, b: None)
        self.clock = clock
        self._task: Optional[asyncio.Task] = None
        self._jobs: set[asyncio.Task] = set()
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------ config + state
    def _load(self) -> dict[str, Any]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def _store(self, data: dict[str, Any]) -> None:
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
        tmp.replace(self.path)

    def config(self) -> dict[str, Any]:
        return {**DEFAULTS, **(self._load().get("config") or {})}

    def set_config(self, changes: dict[str, Any]) -> dict[str, Any]:
        data = self._load()
        cfg = {**DEFAULTS, **(data.get("config") or {})}
        if changes.get("schedule"):
            cfg["weekday"], cfg["time"] = parse_schedule(str(changes["schedule"]))
            cfg["enabled"] = True
        for k in ("enabled",):
            if isinstance(changes.get(k), bool):
                cfg[k] = changes[k]
        for k in ("duration_minutes", "lead_minutes", "lag_ms", "weekday"):
            if k in changes:
                try:
                    cfg[k] = max(0, int(changes[k]))
                except (TypeError, ValueError):
                    raise ValueError(f"{k} must be a number")
        if "time" in changes and not changes.get("schedule"):
            if not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", str(changes["time"])):
                raise ValueError("time must look like 19:00")
            cfg["time"] = str(changes["time"])
        if int(cfg["weekday"]) > 6:
            raise ValueError("weekday is 0 (Monday) to 6 (Sunday)")
        for k in ("discord_channel_id",):
            if k in changes:
                v = str(changes[k] or "").strip()
                if v and not re.fullmatch(r"\d{5,25}", v):
                    raise ValueError("The Discord channel id is a long number (right-click the channel, Copy ID).")
                cfg[k] = v
        for k in ("project", "patch_branch", "backup_target", "name", "message"):
            if k in changes:
                cfg[k] = " ".join(str(changes[k] or "").split())[:400 if k == "message" else 120]
        data["config"] = cfg
        self._store(data)
        return cfg

    def raid_state(self, key: str) -> dict[str, Any]:
        return dict((self._load().get("raids") or {}).get(key) or {"log": []})

    def _save_raid(self, key: str, st: dict[str, Any]) -> None:
        data = self._load()
        raids = data.setdefault("raids", {})
        raids[key] = st
        for old in sorted(raids)[:-12]:   # keep the last dozen raids
            raids.pop(old, None)
        self._store(data)

    def log(self, key: str, text: str) -> None:
        st = self.raid_state(key)
        st.setdefault("log", []).append({"at": time.time(), "text": text[:300]})
        st["log"] = st["log"][-LOG_MAX:]
        self._save_raid(key, st)

    # ------------------------------------------------------------ status
    def status(self) -> dict[str, Any]:
        cfg = self.config()
        now = self.clock()
        ph, start, end = phase(cfg, now)
        key = start.strftime("%Y-%m-%d")
        return {"config": cfg, "schedule": describe_schedule(cfg), "enabled": cfg["enabled"],
                "phase": ph if cfg["enabled"] else "off", "next_start": start.isoformat(),
                "next_end": end.isoformat(), "raid": key, "state": self.raid_state(key),
                "checklist_at": (start - timedelta(minutes=int(cfg["lead_minutes"]))).isoformat()}

    def describe(self) -> str:
        s = self.status()
        if not s["enabled"]:
            return "Raid night isn't set up. Say “Orion, raid night is Fridays at 7”."
        start = datetime.fromisoformat(s["next_start"])
        lines = [f"Raid night: {s['schedule']}. Next one {start.strftime('%a %d %b, %H:%M')}."]
        chk = s["state"].get("checklist")
        if chk:
            lines.append(chk["summary"])
        if s["phase"] == "raid":
            lines.append("The raid is on; Orion is watching the server.")
        return " ".join(lines)

    # ------------------------------------------------------------ checklist
    async def run_checklist(self, key: Optional[str] = None, force: bool = False) -> dict[str, Any]:
        async with self._lock:
            cfg = self.config()
            now = self.clock()
            _, start, _ = phase(cfg, now)
            key = key or start.strftime("%Y-%m-%d")
            checks = []
            for name, fn in (("server", self.server), ("backup", self.backup), ("patch", self.patch)):
                try:
                    ok, text = await fn()
                except Exception as exc:
                    log.exception("raid check %s failed", name)
                    ok, text = False, f"The {name} check broke ({type(exc).__name__})."
                checks.append({"name": name, "ok": ok, "text": text})
            bad = [c for c in checks if c["ok"] is False]
            server_ok = next(c["ok"] for c in checks if c["name"] == "server")
            patch = next(c for c in checks if c["name"] == "patch")
            post = self.draft_announcement(cfg, start, server_ok, patch)
            summary = ("All good for raid night." if not bad else
                       f"{len(bad)} thing{'s' if len(bad) > 1 else ''} need{'s' if len(bad) == 1 else ''} you: " +
                       " ".join(c["text"] for c in bad))
            st = self.raid_state(key)
            st["checklist"] = {"at": time.time(), "checks": checks, "summary": summary, "post": post}
            self._save_raid(key, st)
            decision = None
            if self.board is not None:
                if not force:
                    for d in self.board.pending():
                        if d["task_type"] == TASK_ANNOUNCE and d["packet"]["details"].get("raid") == key:
                            decision = d
                if decision is None:
                    decision = await self.board.propose(
                        TASK_ANNOUNCE, "Raid night: all good, post it?" if not bad else
                        f"Raid night: {len(bad)} check(s) need you. Post the announcement anyway?",
                        {"raid": key, "post": post, "channel": cfg["discord_channel_id"] or "(not set)",
                         "checks": [f"{c['name']}: {c['text']}" for c in checks],
                         "does": "Posts this text in your Discord channel. Nothing is posted unless you approve.",
                         "why_it_asks": "it's a public post"},
                        proposer="Raid Night Autopilot", risk="medium", simulated=False)
                st = self.raid_state(key)
                st["announce_decision"] = decision["id"]
                self._save_raid(key, st)
            self.notify("Raid night check", summary + " The announcement is ready for your OK.")
            return {"raid": key, "checks": checks, "summary": summary, "post": post,
                    "decision_id": decision["id"] if decision else None}

    @staticmethod
    def draft_announcement(cfg: dict[str, Any], start: datetime, server_ok: Optional[bool],
                           patch: dict[str, Any]) -> str:
        server_line = "The server is up and ready. " if server_ok else ""
        b = re.match(r"^Patch (\S+) is tested", patch.get("text") or "")
        patch_line = f"New patch going in: {b.group(1)}. " if b and patch.get("ok") else ""
        try:
            text = str(cfg.get("message") or DEFAULTS["message"]).format(
                name=cfg.get("name") or "Raid night", time=nice_time(start.strftime("%H:%M")),
                day=start.strftime("%A"), server_line=server_line, patch_line=patch_line)
        except (KeyError, IndexError, ValueError):
            text = DEFAULTS["message"].format(name=cfg.get("name") or "Raid night",
                                              time=nice_time(start.strftime("%H:%M")), day=start.strftime("%A"),
                                              server_line=server_line, patch_line=patch_line)
        text = " ".join(text.split())
        try:
            from outreach_engine import humanize_copy   # the comms drafter's plain-voice filter
            text = humanize_copy(text)
        except Exception:
            pass
        return with_disclosure(text)

    # ------------------------------------------------------------ posting (after your approval)
    def execute_post(self, d: dict[str, Any], spawn: Callable[[Awaitable[Any]], Any], what: str) -> dict[str, Any]:
        det = d["packet"]["details"]
        text = with_disclosure(str(det.get("post") or ""))
        channel = self.config().get("discord_channel_id") or ""

        async def go() -> None:
            ok = False
            if self.poster is not None and channel:
                try:
                    ok = bool(await self.poster(channel, text))
                except Exception as exc:
                    log.warning("Raid post failed: %s", exc)
            key = det.get("raid") or ""
            if key:
                st = self.raid_state(key)
                st[f"{what}_posted"] = ok
                st[f"{what}_text"] = text
                self._save_raid(key, st)
            if ok:
                self.notify("Posted on Discord", text[:200])
            else:
                self.notify("Approved, but not posted", "Discord isn't connected (or no channel is set), so copy "
                                                        "it from Orion: " + text[:300])
        spawn(go())
        return {"state": "started", "channel": channel}

    # ------------------------------------------------------------ during and after
    async def watch(self, key: str) -> None:
        cfg = self.config()
        st = self.raid_state(key)
        try:
            ok, text = await self.server()
        except Exception:
            ok, text = None, ""
        was_down = st.get("server_down", False)
        if ok is False and not was_down:
            self.log(key, f"Server went down: {text}")
            st = self.raid_state(key)
            st["server_down"] = True
            st["crashes"] = int(st.get("crashes", 0)) + 1
            self._save_raid(key, st)
            self.notify("OpenWoW went down during the raid", "Approve the restart on your phone.")
            if self.restart is not None:
                try:
                    await self.restart(text)
                except Exception:
                    log.exception("restart proposal failed")
        elif ok is True and was_down:
            self.log(key, "Server is back up.")
            st = self.raid_state(key)
            st["server_down"] = False
            self._save_raid(key, st)
        if self.lag_probe is not None:
            try:
                ms = await self.lag_probe()
            except Exception:
                ms = None
            if ms is not None and ms >= float(cfg.get("lag_ms") or 250):
                st = self.raid_state(key)
                last = float(st.get("last_lag_at", 0))
                st["lag_spikes"] = int(st.get("lag_spikes", 0)) + 1
                st["worst_lag_ms"] = max(float(st.get("worst_lag_ms", 0)), float(ms))
                st["last_lag_at"] = time.time()
                self._save_raid(key, st)
                if time.time() - last > 300:   # one log line per lag episode, not every minute
                    self.log(key, f"Lag spike: {int(ms)} ms.")

    def crash_seen(self, status_text: str) -> None:
        """wow agent crash hook: note it in tonight's log while a raid is on."""
        cfg = self.config()
        if not cfg["enabled"]:
            return
        ph, start, _ = phase(cfg, self.clock())
        if ph == "raid":
            first = (status_text or "").strip().splitlines()[:1]
            self.log(start.strftime("%Y-%m-%d"), "Crash reported by the wow agent" + (f": {first[0][:120]}" if first
                                                                                         else "."))

    def notes_text(self, key: str) -> str:
        st = self.raid_state(key)
        cfg = self.config()
        day = datetime.strptime(key, "%Y-%m-%d").strftime("%A %d %B")
        lines = [f"{cfg.get('name') or 'Raid night'} notes ({day}):"]
        crashes = int(st.get("crashes", 0))
        lines.append("The server stayed up the whole raid." if not crashes else
                     f"The server went down {crashes} time{'s' if crashes > 1 else ''}; restarts waited for an OK.")
        if st.get("lag_spikes"):
            lines.append(f"{st['lag_spikes']} lag spike{'s' if st['lag_spikes'] > 1 else ''} "
                         f"(worst {int(st.get('worst_lag_ms', 0))} ms).")
        for e in st.get("log", [])[-8:]:
            if not e["text"].startswith(("Lag spike", "Server went down", "Server is back")):
                lines.append(f"- {datetime.fromtimestamp(e['at']).strftime('%H:%M')} {e['text']}")
        lines.append("Thanks for raiding!")
        return with_disclosure(" ".join(lines[:2]) + ("\n" + "\n".join(lines[2:]) if len(lines) > 2 else ""))

    async def draft_notes(self, key: str) -> dict[str, Any]:
        text = self.notes_text(key)
        st = self.raid_state(key)
        st["notes"] = text
        st["notes_drafted"] = True
        self._save_raid(key, st)
        dec = None
        if self.board is not None:
            dec = await self.board.propose(
                TASK_NOTES, "Post the raid notes on Discord?",
                {"raid": key, "post": text, "channel": self.config()["discord_channel_id"] or "(not set)",
                 "does": "Posts these notes in your Discord channel. Reject keeps them private.",
                 "why_it_asks": "it's a public post"},
                proposer="Raid Night Autopilot", risk="low", simulated=False)
        self.notify("Raid notes are ready", "Have a look and post them if you like.")
        return {"raid": key, "notes": text, "decision_id": dec["id"] if dec else None}

    # ------------------------------------------------------------ the clock
    async def tick(self) -> Optional[str]:
        cfg = self.config()
        if not cfg["enabled"]:
            return None
        ph, start, _ = phase(cfg, self.clock())
        key = start.strftime("%Y-%m-%d")
        st = self.raid_state(key)
        if ph in ("checklist", "raid") and not st.get("checklist"):
            await self.run_checklist(key)
            return "checklist"
        if ph == "raid":
            if not st.get("started"):
                st["started"] = True
                self._save_raid(key, st)
                self.log(key, "Raid started.")
            await self.watch(key)
            return "watch"
        if ph == "after" and st.get("started") and not st.get("notes_drafted"):
            self.log(key, "Raid ended.")
            await self.draft_notes(key)
            return "notes"
        return None

    def start(self) -> None:
        async def loop() -> None:
            while True:
                await asyncio.sleep(TICK_S)
                try:
                    await self.tick()
                except Exception:
                    log.exception("raid night tick failed")
        if self._task is None:
            self._task = asyncio.get_running_loop().create_task(loop(), name="raid-night")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            self._task = None


def with_disclosure(text: str) -> str:
    text = (text or "").rstrip()
    return text if DISCLOSURE.lower() in text.lower() else f"{text}\n\n{DISCLOSURE}"


async def _none(text: str) -> tuple[Optional[bool], str]:
    return None, text


def _register_task_types() -> None:
    import boardroom
    from boardroom import TaskType
    for t in (TaskType(TASK_ANNOUNCE, "Post the raid night announcement", "community", reversible=False,
                       money=False, sensitive=True),
              TaskType(TASK_NOTES, "Post the raid notes", "community", reversible=False, money=False,
                       sensitive=True)):
        boardroom.TASK_TYPES.setdefault(t.key, t)


_register_task_types()
