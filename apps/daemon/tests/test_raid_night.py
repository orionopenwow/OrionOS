"""Raid Night Autopilot (Toolkit 24): checklist 2 hours before, a drafted Discord post that is only posted after a
phone approval and always says it was written with Orion's help, watching during the raid, notes after."""
import asyncio
import time
import types
from datetime import datetime, timedelta, timezone

import pytest

import boardroom as br
import raid_night as rn

TZ = timezone(timedelta(hours=2))          # any local time zone
FRI_1730 = datetime(2026, 10, 16, 17, 30, tzinfo=TZ)   # 2026-10-16 is a Friday


class Clock:
    def __init__(self, t):
        self.t = t

    def __call__(self):
        return self.t


def ok(text):
    async def check():
        return True, text
    return check


class H:
    def __init__(self, tmp_path, server=None, backup=None, patch=None, clock=FRI_1730):
        self.loop = asyncio.new_event_loop()
        self.board = br.Boardroom(tmp_path / "board")
        self.posts, self.notes, self.restarts = [], [], []
        self.lag = [None]

        async def poster(channel, text):
            self.posts.append((channel, text))
            return True

        async def restart(why):
            self.restarts.append(why)

        async def lag():
            return self.lag[0]
        self.clock = Clock(clock)
        self.raid = rn.RaidNight(tmp_path / "data", board=self.board, server=server or ok("Server is up: OpenWoW: running"),
                                 backup=backup, patch=patch, poster=poster, restart=restart, lag_probe=lag,
                                 notify=lambda t, b: self.notes.append((t, b)), clock=self.clock)
        self.jobs = []
        for task, what in (("raid.announce", "announce"), ("raid.notes", "notes")):
            self.board.executors[task] = (lambda w: lambda d: self.raid.execute_post(
                d, lambda c: self.jobs.append(self.loop.create_task(c)), w))(what)

    def run(self, c):
        return self.loop.run_until_complete(c)

    def verdict(self, task_type, v="approve"):
        d = next(x for x in self.board.pending() if x["task_type"] == task_type)
        out = self.run(self.board.verdict(d["id"], v, br.packet_hash(d["packet"])))
        while self.jobs:
            self.run(self.jobs.pop(0))
        return out


@pytest.mark.parametrize("text,expected", [
    ("Fridays at 7", (4, "19:00")), ("fridays at 7 pm", (4, "19:00")), ("every Fri at 7:30pm", (4, "19:30")),
    ("saturday 20:15", (5, "20:15")), ("Sundays at 11 am", (6, "11:00")), ("wed at 12 pm", (2, "12:00")),
])
def test_parse_schedule(text, expected):
    assert rn.parse_schedule(text) == expected


@pytest.mark.parametrize("bad", ["at 7", "fridays", "friday at 25"])
def test_parse_schedule_refuses_nonsense(bad):
    with pytest.raises(ValueError):
        rn.parse_schedule(bad)


def test_phases_in_local_time():
    cfg = {**rn.DEFAULTS, "weekday": 4, "time": "19:00"}
    assert rn.phase(cfg, FRI_1730)[0] == "checklist"
    assert rn.phase(cfg, FRI_1730 - timedelta(hours=1))[0] == "idle"
    assert rn.phase(cfg, FRI_1730 + timedelta(hours=2))[0] == "raid"
    ph, start, _ = rn.phase(cfg, FRI_1730 + timedelta(hours=5))
    assert ph == "after" and start.day == 16
    ph, start, _ = rn.phase(cfg, FRI_1730 + timedelta(days=1))
    assert ph == "idle" and start == datetime(2026, 10, 23, 19, 0, tzinfo=TZ)


def test_checklist_drafts_the_post_and_posts_only_after_approval(tmp_path):
    class Backups:
        def last_backup(self, target):
            return {"at": time.time() - 3600 * 10}  # relative to the real clock the check uses

        async def verify_restore(self, target):
            return {"ok": True}
    h = H(tmp_path, backup=rn.default_backup_check(types.SimpleNamespace(backups=Backups()), lambda: "openwow"),
          patch=ok("Patch patch/loot-fix is tested and ready."))
    h.raid.set_config({"schedule": "Fridays at 7 pm", "discord_channel_id": "123456789012345678"})
    res = h.run(h.raid.run_checklist())
    assert res["summary"] == "All good for raid night."
    assert [c["ok"] for c in res["checks"]] == [True, True, True]
    assert "restores fine" in res["checks"][1]["text"]
    assert res["post"].endswith(rn.DISCLOSURE) and "7 pm" in res["post"] and "patch/loot-fix" in res["post"]
    d = h.board.pending()[0]
    assert d["task_type"] == "raid.announce" and d["packet"]["summary"] == "Raid night: all good, post it?"
    assert br.TASK_TYPES["raid.announce"].max_level == 1       # public posting never becomes automatic
    assert h.posts == []                                        # nothing posted by itself
    assert h.run(h.raid.run_checklist())["decision_id"] == d["id"]   # no duplicate card
    h.verdict("raid.announce")
    assert h.posts == [("123456789012345678", res["post"])]


def test_problems_are_named_and_a_rejected_post_stays_unposted(tmp_path):
    async def down():
        return False, "The OpenWoW server isn't running (last error: boom)."
    h = H(tmp_path, server=down)
    h.raid.set_config({"schedule": "Fridays at 7"})
    res = h.run(h.raid.run_checklist())
    assert res["summary"].startswith("1 thing needs you: The OpenWoW server isn't running")
    assert "Backups aren't set up yet" in res["checks"][1]["text"] and res["checks"][1]["ok"] is None
    assert "server is up" not in res["post"].lower()
    h.verdict("raid.announce", "reject")
    assert h.posts == []


def test_backup_tool_missing_or_failing(tmp_path):
    check = rn.default_backup_check(types.SimpleNamespace(), lambda: "openwow")
    ok_, text = asyncio.run(check())
    assert ok_ is None and "Backups aren't set up" in text

    class Bad:
        def last_backup(self, t):
            return {"at": 0}
    ok_, text = asyncio.run(rn.default_backup_check(types.SimpleNamespace(backups=Bad()), lambda: "openwow")())
    assert ok_ is False and "hours old" in text


def test_edited_disclosure_cannot_be_dropped():
    assert rn.with_disclosure("Raid at 7!").endswith("(Written with Orion's help.)")
    assert rn.with_disclosure("Raid! (written with orion's help.)").count("help") == 1


def test_not_posted_when_discord_is_not_connected(tmp_path):
    h = H(tmp_path)
    h.raid.poster = None
    h.raid.set_config({"schedule": "Fridays at 7"})
    h.run(h.raid.run_checklist())
    h.verdict("raid.announce")
    assert h.notes[-1][0] == "Approved, but not posted" and "copy it" in h.notes[-1][1]


def test_the_clock_checks_watches_and_writes_notes(tmp_path):
    state = {"up": True}

    async def server():
        return (True, "Server is up") if state["up"] else (False, "The OpenWoW server isn't running.")
    h = H(tmp_path, server=server, clock=FRI_1730 - timedelta(hours=2))
    assert h.run(h.raid.tick()) is None                       # not set up: nothing happens
    h.raid.set_config({"schedule": "Fridays at 7", "lag_ms": 200})
    assert h.run(h.raid.tick()) is None                       # 15:30: too early
    h.clock.t = FRI_1730
    assert h.run(h.raid.tick()) == "checklist"
    assert h.run(h.raid.tick()) is None                       # once per raid
    h.clock.t = FRI_1730 + timedelta(hours=2)                 # 19:30, raid is on
    assert h.run(h.raid.tick()) == "watch"
    state["up"] = False
    h.run(h.raid.tick())
    assert h.restarts and "isn't running" in h.restarts[0]    # restart goes through the approval rules
    h.run(h.raid.tick())
    assert len(h.restarts) == 1                               # once per outage
    state["up"] = True
    h.lag[0] = 340.0
    h.run(h.raid.tick())
    h.raid.crash_seen("OpenWoW: crashed\nLast error: boom")
    st = h.raid.status()["state"]
    texts = [e["text"] for e in st["log"]]
    assert "Raid started." in texts and any(t.startswith("Server went down") for t in texts)
    assert "Server is back up." in texts and "Lag spike: 340 ms." in texts
    assert any(t.startswith("Crash reported by the wow agent") for t in texts)
    h.clock.t = FRI_1730 + timedelta(hours=5, minutes=30)     # 23:00, raid over
    assert h.run(h.raid.tick()) == "notes"
    d = next(x for x in h.board.pending() if x["task_type"] == "raid.notes")
    notes = d["packet"]["details"]["post"]
    assert "went down 1 time" in notes and "1 lag spike (worst 340 ms)" in notes and notes.endswith(rn.DISCLOSURE)
    assert h.run(h.raid.tick()) is None                       # notes drafted once


def test_config_validation(tmp_path):
    h = H(tmp_path)
    with pytest.raises(ValueError):
        h.raid.set_config({"discord_channel_id": "general"})
    with pytest.raises(ValueError):
        h.raid.set_config({"time": "7pm"})
    cfg = h.raid.set_config({"schedule": "Saturdays at 8:30 pm", "lead_minutes": 90})
    assert (cfg["weekday"], cfg["time"], cfg["lead_minutes"], cfg["enabled"]) == (5, "20:30", 90, True)
    assert "Saturdays at 8:30 pm" in h.raid.describe()
