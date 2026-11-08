"""Test Center "Do it for me" (test_center_auto.py): every one-click action and every watcher that ticks an item off
by itself, against fake Orion parts. Also: the Test Center never approves, applies, merges or sends anything.
No network: the practice page is served on 127.0.0.1 and fetched there."""
from __future__ import annotations

import asyncio
import json
import time
import urllib.request
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import test_center
import test_center_auto as auto
from test_center import TestCenter
from test_test_center import item, make_daemon, phone, quick_pass, wait_done


def run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


@pytest.fixture
def tc(tmp_path):
    d = make_daemon(phones=phone(int(time.time() * 1000)))
    c = TestCenter(d, tmp_path, platform="other", which=lambda b: f"/usr/bin/{b}" if b == "claude" else None)
    d.test_center = c
    c.opened: list[str] = []
    c.browser = lambda url: c.opened.append(url) or True
    c.practice_port = 0
    c.practice_wait_s = 0
    yield c
    if c._practice is not None:
        c._practice.stop()


def opened(tc, what):
    return [e for e in tc.daemon.bus.events if e.get("event") == "HUD_OPEN" and e.get("what") == what]


async def act_then_refresh(tc, iid):
    out = await tc.run_one(iid)
    tc._last_detect.clear()
    await tc.refresh()
    return out


# ------------------------------------------------------------------ the shape every item has now
def test_every_item_has_do_ask_watch_and_manual_items_can_mostly_do_it_for_you(tc):
    v = tc.view()
    flat = [x for g in v["groups"] for x in g["items"]]
    for x in flat:
        assert {"do", "ask", "watch", "armed", "by_phone"} <= set(x), x["id"]
    # manual PC items with nothing the PC can do or see are the exception, and few
    left = [x["id"] for x in flat if x["kind"] == "manual" and x["where"] == "pc" and not x["do"] and not x["watch"]]
    assert set(left) <= {"os_restart_windows"}, left
    assert v["all_done"] is False


def test_all_done_when_everything_is_pass_or_skip(tc):
    for it in tc.items():
        tc._set(it.id, "pass" if it.kind == "auto" else "skip", "x")
    v = tc.view()
    assert v["all_done"] is True and v["next"] is None
    tc._set("glass_windows", "fail", "no")
    assert tc.view()["all_done"] is False


# ------------------------------------------------------------------ Yes / No questions
def test_speaks_then_asks_did_you_hear_it(tc):
    out = run(tc.run_one("tts_speaks"))
    assert tc.daemon.spoken and out["status"] == "todo"
    assert out["ask"] and "hear" in out["ask"].lower() and out["next"] == out["ask"]
    out = tc.set_result("tts_speaks", "pass")
    assert out["status"] == "pass" and out["ask"] is None


def test_glass_asks_a_question(tc):
    out = run(tc.run_one("glass_windows"))
    assert out["ask"] and "black hole" in out["ask"]
    assert tc.set_result("glass_windows", "todo")["ask"] is None


# ------------------------------------------------------------------ phone
def test_pairing_ticks_itself_off(tc):
    tc.daemon.board.store.kv["phones"] = json.dumps({})
    run(tc._run_check(tc.by_id["phone_paired"]))
    x = item(tc.view(), "phone_paired")
    assert x["status"] == "fail" and x["do"] == "Open pairing"
    run(tc.run_one("phone_paired"))                                   # the fix action, not the check
    assert opened(tc, "phone")
    tc.daemon.board.store.kv["phones"] = json.dumps(phone(int(time.time() * 1000)))
    assert item(run(tc.view_fresh()), "phone_paired")["status"] == "pass"


def test_phone_draft_is_seen(tc):
    tc.daemon.phone_pair.phone_draft_at = 0
    assert item(run(tc.view_fresh()), "phone_draft")["status"] == "todo"
    tc.daemon.phone_pair.phone_draft_at = time.time() + 1
    tc._last_detect.clear()
    x = item(run(tc.view_fresh()), "phone_draft")
    assert x["status"] == "pass" and "sent nothing" in x["detail"]


def test_phone_pair_marks_the_draft_time_where_the_watcher_reads_it():
    src = (Path(__file__).resolve().parents[1] / "phone_pair.py").read_text(encoding="utf-8")
    assert "self.phone_draft_at = time.time()" in src


def test_phone_result_with_a_measurement_becomes_the_detail(tc):
    out = tc.set_result("phone_notifications", "fail", "Needed for the one inbox.", by="phone")
    assert out["status"] == "fail" and out["detail"] == "Needed for the one inbox."
    assert out["by_phone"] is True


# ------------------------------------------------------------------ a real mission, projects
class FakeMissions:
    def __init__(self) -> None:
        self.made: list[dict] = []
        self.calls: list[str] = []

    def create(self, title, repo=None, agent=None, budget_usd=None, source="hud", **kw):
        self.calls.append("create")
        m = {"id": f"m{len(self.made) + 1}", "title": title, "repo": repo, "agent": agent, "source": source,
             "state": "running"}
        self.made.append(m)
        return m

    def get(self, mid):
        self.calls.append("get")
        return next((m for m in self.made if m["id"] == mid), None)

    def list(self):
        return []


def test_real_mission_starts_on_a_test_repo_and_passes_at_review(tc):
    mc = FakeMissions()
    tc.daemon.mission_control = mc
    out = run(act_then_refresh(tc, "real_mission"))
    assert out["armed"] and mc.made[0]["agent"] == "claude" and mc.made[0]["source"] == "test-center"
    assert Path(mc.made[0]["repo"], ".git").is_dir()
    assert item(tc.view(), "real_mission")["status"] == "todo"
    mc.made[0]["state"] = "review"
    tc._last_detect.clear()
    assert item(run(tc.view_fresh()), "real_mission")["status"] == "pass"
    assert set(mc.calls) <= {"create", "get"}                         # never merges


def test_real_mission_skips_without_claude(tmp_path):
    d = make_daemon(phones=phone(int(time.time() * 1000)))
    c = TestCenter(d, tmp_path, platform="other", which=lambda b: None)
    assert run(c.run_one("real_mission"))["status"] == "skip"


class FakeProjects:
    def __init__(self) -> None:
        self.items: list[dict] = [{"name": "Star Catcher Test", "created": 1, "latest": None}]

    def list(self):
        return self.items


def test_start_project_opens_the_wizard_prefilled_and_passes_at_review(tc):
    pj = FakeProjects()
    tc.daemon.projects = pj
    run(tc.run_one("start_project"))
    (ev,) = opened(tc, "projects")
    assert ev["prefill"]["name"] == "Star Catcher Test 2" and ev["prefill"]["starter"] == "web-game"
    assert ev["prefill"]["idea"]
    pj.items.append({"name": "Star Catcher Test 2", "created": time.time() + 1, "latest": {"state": "running"}})
    assert item(run(tc.view_fresh()), "start_project")["status"] == "todo"
    pj.items[-1]["latest"]["state"] = "review"
    tc._last_detect.clear()
    assert item(run(tc.view_fresh()), "start_project")["status"] == "pass"


def test_an_old_project_at_review_doesnt_count(tc):
    pj = FakeProjects()
    pj.items[0]["latest"] = {"state": "review"}                        # made long before this test round
    tc.daemon.projects = pj
    assert item(run(tc.view_fresh()), "start_project")["status"] == "todo"


# ------------------------------------------------------------------ skills
class FakeRunner:
    def __init__(self) -> None:
        self.runs: dict = {}


class FakeHub:
    def __init__(self) -> None:
        self.skills: list[dict] = []
        self.store = SimpleNamespace(summaries=lambda: list(self.skills))
        self.runner = FakeRunner()
        self.desktop = SimpleNamespace(runner=FakeRunner(), start_recording=self._desk_start,
                                       stop_recording=self._desk_stop)
        self.recording = None
        self.log: list[tuple] = []
        self.extension = True

    def state(self):
        return {"recording": self.recording}

    async def start_recording(self):
        self.log.append(("start",))
        if self.extension:
            self.recording = {"rec_id": "r1", "surface": "browser"}
        return self.extension

    async def stop_recording(self):
        self.log.append(("stop",))
        self.recording = None
        return True

    async def _desk_start(self):
        self.log.append(("desktop-start",))
        self.recording = {"rec_id": "desktop", "surface": "desktop"}
        return True, ""

    async def _desk_stop(self):
        self.log.append(("desktop-stop",))
        self.recording = None

    async def run_skill(self, skill_id, values=None, *, mode="step", preview=True, source="user"):
        self.log.append(("run", skill_id, mode, preview))
        r = SimpleNamespace(skill={"id": skill_id, "name": skill_id}, status="running", started_at=time.time())
        self.runner.runs[skill_id] = r
        return r


def _fetch(url, method="GET"):
    req = urllib.request.Request(url, method=method, data=b"" if method == "POST" else None)
    with urllib.request.urlopen(req, timeout=5) as r:       # 127.0.0.1 only
        return r.status, r.read().decode()


def test_record_opens_the_practice_page_records_and_stops_on_finish(tc):
    hub = FakeHub()
    tc.daemon.skills = hub

    async def go():
        out = await tc.run_one("record_skill")
        page = tc._practice
        assert page.url.startswith("http://127.0.0.1:")
        code, html = await asyncio.to_thread(_fetch, page.url)
        assert code == 200 and "practice-finish" in html and "<form" not in html
        assert hub.recording is not None
        await asyncio.to_thread(_fetch, page.url + "done", "POST")    # you pressed Finish
        for _ in range(50):
            if hub.recording is None:
                break
            await asyncio.sleep(0.02)
        return out
    out = run(go())
    assert tc.opened == [tc._practice.url] and ("stop",) in hub.log and hub.recording is None
    assert out["armed"] and opened(tc, "skills")
    assert item(run(tc.view_fresh()), "record_skill")["status"] == "todo"
    hub.skills.append({"id": "orion_practice", "name": "Orion practice", "steps": 3, "updated_at": time.time() + 1,
                       "start_url": tc._practice.url})
    tc._last_detect.clear()
    x = item(run(tc.view_fresh()), "record_skill")
    assert x["status"] == "pass" and "3 steps" in x["detail"]


def test_record_falls_back_to_the_desktop_recorder(tc):
    hub = FakeHub()
    hub.extension = False
    tc.daemon.skills = hub
    out = run(tc.run_one("record_skill"))
    assert ("desktop-start",) in hub.log and "desktop" in out["detail"]


def test_practice_skill_replays_by_itself_other_skills_wait_for_start(tc):
    hub = FakeHub()
    tc.daemon.skills = hub

    async def go():
        page = auto.practice_page(tc)
        hub.skills[:] = [{"id": "mine", "name": "Mine", "updated_at": 5, "start_url": "https://example.org/"}]
        await tc.run_one("replay_skill")
        hub.skills.append({"id": "orion_practice", "name": "Practice", "updated_at": 1, "start_url": page.url})
        await tc.run_one("replay_skill")
    run(go())
    runs = [x for x in hub.log if x[0] == "run"]
    assert runs[0] == ("run", "mine", "step", True)                   # yours: preview, you press Start
    assert runs[1] == ("run", "orion_practice", "all", False)        # the practice skill on the practice page
    assert item(run(tc.view_fresh()), "replay_skill")["status"] == "todo"
    hub.runner.runs["orion_practice"].status = "done"
    tc._last_detect.clear()
    assert item(run(tc.view_fresh()), "replay_skill")["status"] == "pass"


def test_replay_without_a_skill_says_so(tc):
    tc.daemon.skills = FakeHub()
    out = run(tc.run_one("replay_skill"))
    assert out["status"] == "todo" and "Record" in out["detail"]


# ------------------------------------------------------------------ the practice screen change
class FakeSelfProg:
    def __init__(self, root: Path, status_after="ready") -> None:
        self.root, self.data = root, root / "data"
        self.changes: dict = {}
        self.status_after = status_after
        self.calls: list[str] = []
        self.emitted: list[dict] = []

    def view_root(self):
        return self.root

    def new_change(self, proposal):
        self.calls.append("new_change")
        c = {**proposal, "status": "building"}
        self.changes[c["id"]] = c
        return c

    def build_and_test(self, cid):
        self.calls.append("build_and_test")
        self.changes[cid]["status"] = self.status_after
        if self.status_after == "failed":
            self.changes[cid]["error"] = "Type check: broken"
        return self.changes[cid]

    def _emit(self, ev):
        self.emitted.append(ev)

    @staticmethod
    def public(c, diff=True):
        return {k: v for k, v in c.items() if k != "files"}

    def get(self, cid):
        return self.changes.get(cid)

    def list(self):
        return list(self.changes.values())

    # these must never be called by the Test Center
    def stage(self, *a, **k):
        self.calls.append("stage")

    def approve(self, *a, **k):
        self.calls.append("approve")

    def undo_last(self, **k):
        self.calls.append("undo_last")
        for c in self.changes.values():
            c["status"], c["rolled_back_at"] = "rolled_back", time.time() + 1
        return {}


def _banner_root(tmp_path):
    p = tmp_path / "repo" / auto.BANNER_PATH
    p.parent.mkdir(parents=True)
    p.write_text(auto._banner(auto.BANNER_COLOURS[0]), encoding="utf-8")
    return tmp_path / "repo"


def test_practice_change_builds_a_ghost_preview_and_is_never_applied(tc, tmp_path):
    sp = FakeSelfProg(_banner_root(tmp_path))
    tc.daemon.selfprog = sp

    async def go():
        out = await tc.run_one("ghost_preview")
        await asyncio.gather(*list(tc._bg))
        tc._last_detect.clear()
        await tc.refresh()
        return out
    out = run(go())
    assert out["armed"] and sp.calls == ["new_change", "build_and_test"]
    assert "stage" not in sp.calls and "approve" not in sp.calls
    (c,) = sp.changes.values()
    assert [f["path"] for f in c["files"]] == [auto.BANNER_PATH]
    assert auto.BANNER_COLOURS[1] in c["files"][0]["new_content"] and c["diff"].startswith("---")
    assert any(e["event"] == "SELFPROG_CHANGE" for e in sp.emitted)
    x = item(tc.view(), "ghost_preview")
    assert x["status"] == "pass" and "up to you" in x["detail"]


def test_practice_change_that_fails_its_checks_fails_the_item(tc, tmp_path):
    tc.daemon.selfprog = FakeSelfProg(_banner_root(tmp_path), status_after="failed")

    async def go():
        await tc.run_one("ghost_preview")
        await asyncio.gather(*list(tc._bg))
        tc._last_detect.clear()
        await tc.refresh()
    run(go())
    x = item(tc.view(), "ghost_preview")
    assert x["status"] == "fail" and "Type check" in x["detail"]


def test_practice_proposal_is_harmless_screen_code_and_flips_back():
    import protected_core
    import self_program
    first = auto.practice_proposal(auto._banner(auto.BANNER_COLOURS[0]))
    after = first["files"][0]["new_content"]
    second = auto.practice_proposal(after)
    assert auto.BANNER_COLOURS[0] in second["files"][0]["new_content"]
    assert self_program.CHANGE_ID.match(first["id"])
    assert self_program._ui_only(auto.BANNER_PATH)
    assert not protected_core.check([{"path": auto.BANNER_PATH, "before": "", "after": after}])
    created = auto.practice_proposal(None)                             # layer mode: no copy of the screen code
    assert created["files"][0]["action"] == "create"


def test_practice_banner_file_in_the_repo_matches_what_the_proposal_expects():
    repo = Path(__file__).resolve().parents[3]
    text = (repo / auto.BANNER_PATH).read_text(encoding="utf-8")
    assert "PRACTICE_COLOUR = '#" in text
    assert auto.practice_proposal(text)["files"][0]["new_content"] != text


# ------------------------------------------------------------------ undo
def test_undo_only_ever_undoes_the_practice_change(tc, tmp_path, monkeypatch):
    import orion_layer
    sp = FakeSelfProg(tmp_path)
    tc.daemon.selfprog = sp
    tc.state["practice_change"] = "patch-0000abcd"
    sp.changes["patch-0000abcd"] = {"id": "patch-0000abcd", "status": "applied", "summary": "Practice"}
    swaps = [{"change_id": "patch-1111ffff", "status": "kept"}]          # someone else's change is the latest
    monkeypatch.setattr(orion_layer, "history", lambda dd=None: swaps)
    run(tc.run_one("undo_last"))
    assert "undo_last" not in sp.calls and opened(tc, "selfchanges")
    swaps.append({"change_id": "patch-0000abcd", "status": "kept"})
    run(act_then_refresh(tc, "undo_last"))
    assert sp.calls.count("undo_last") == 1
    assert item(tc.view(), "undo_last")["status"] == "pass"


def test_undo_from_the_change_history_is_seen(tc):
    rows = [{"undoes": "chg-1", "ts": time.time() + 1, "what": "Turned the hot mic back off"}]
    tc.change_log = SimpleNamespace(own=lambda n: rows)
    x = item(run(tc.view_fresh()), "undo_last")
    assert x["status"] == "pass" and "hot mic" in x["detail"]


# ------------------------------------------------------------------ privacy
class FakePrivacy:
    def __init__(self, stuck=()) -> None:
        self.on = {"hotmic": True, "screen": True, "camera": False}
        self.stuck = set(stuck)

    async def off_all(self):
        for k in self.on:
            if k not in self.stuck:
                self.on[k] = False
        return {"ok": not self.stuck, "message": "done"}

    def snapshot(self):
        return {"sensors": [{"id": k, "label": k.title(), "on": v} for k, v in self.on.items()]}


def test_privacy_turns_everything_off_and_reads_every_switch_back(tc):
    tc.daemon.privacy_dashboard = FakePrivacy()
    out = run(tc.run_one("privacy_off"))
    assert out["status"] == "pass" and "all 3 switches" in out["detail"]
    assert opened(tc, "privacy")


def test_privacy_fails_when_a_switch_stays_on(tc):
    tc.daemon.privacy_dashboard = FakePrivacy(stuck={"screen"})
    out = run(tc.run_one("privacy_off"))
    assert out["status"] == "fail" and "Screen" in out["detail"]


# ------------------------------------------------------------------ OrionOS
def _os_tc(tmp_path, profile="image"):
    d = make_daemon(phones=phone(int(time.time() * 1000)))
    return TestCenter(d, tmp_path, platform="orionos", profile=profile, which=lambda b: None)


def test_usb_boot_and_vault_are_seen(tmp_path):
    c = _os_tc(tmp_path)
    c.vault_device = tmp_path / "orionvault"
    v = run(c.view_fresh())
    assert item(v, "os_usb_boot")["status"] == "pass" and item(v, "os_vault")["status"] == "todo"
    c.vault_device.write_text("")
    c._last_detect.clear()
    assert item(run(c.view_fresh()), "os_vault")["status"] == "pass"


def test_sleep_is_noticed_then_asks_about_the_lock(tmp_path):
    c = _os_tc(tmp_path, profile="baremetal")
    stats = tmp_path / "success"
    stats.write_text("4\n")
    c.suspend_stats = stats
    run(c.run_one("os_sleep_lock"))
    assert c.state["suspend_base"] == 4
    assert item(run(c.view_fresh()), "os_sleep_lock")["ask"] is None
    stats.write_text("5\n")
    c._last_detect.clear()
    x = item(run(c.view_fresh()), "os_sleep_lock")
    assert x["status"] == "todo" and "password" in x["ask"]
    assert c.set_result("os_sleep_lock", "pass")["status"] == "pass"


# ------------------------------------------------------------------ Windows
def _win_tc(tmp_path):
    d = make_daemon(phones=phone(int(time.time() * 1000)))
    c = TestCenter(d, tmp_path, platform="windows", which=lambda b: None)
    c.desktop_dirs = [tmp_path / "Desktop"]
    c.desktop_dirs[0].mkdir()
    return c


def test_shortcut_found_then_asks(tmp_path):
    c = _win_tc(tmp_path)
    out = run(c.run_one("win_shortcut"))
    assert "no Orion shortcut" in out["detail"]
    (c.desktop_dirs[0] / "Orion.lnk").write_bytes(b"x")
    c._last_detect.clear()
    x = item(run(c.view_fresh()), "win_shortcut")
    assert x["ask"] and "shortcut" in x["ask"]


class FakePhoneLink:
    def __init__(self, ok=False) -> None:
        self.ok, self.opened, self.port = ok, 0, True

    def port_running(self):
        return self.port

    async def ensure_port(self):
        self.port = True
        return True

    def lan(self, fresh=False):
        if self.ok:
            return {"ok": True, "words": "Your phone can reach this PC on your home Wi-Fi."}
        return {"ok": False, "fix": "open_access", "words": "Windows Firewall stops your phone reaching this PC."}

    def open_access(self):
        self.opened += 1
        return {"ok": True, "words": "A window opened. Say yes when Windows asks for permission."}


def test_phone_access_opens_the_firewall_step_and_ticks_itself(tmp_path):
    c = _win_tc(tmp_path)
    pl = FakePhoneLink()
    c.daemon.phone_link = pl
    out = run(c.run_one("win_phone_access"))
    assert pl.opened == 1 and out["status"] == "todo" and out["armed"]
    pl.ok = True
    c._last_detect.clear()
    assert item(run(c.view_fresh()), "win_phone_access")["status"] == "pass"


def test_readiness_check_runs_and_reads_its_report(tmp_path):
    c = _win_tc(tmp_path)
    c.repo_root = tmp_path
    (tmp_path / auto.READY_CMD).write_text("@echo off\r\n")
    started = []
    c.popen = lambda args, **kw: started.append(args)
    run(c.run_one("win_ready"))
    assert started and started[0][-1].endswith(auto.READY_CMD)
    report = c.desktop_dirs[0] / auto.READY_REPORT
    report.write_text("...\nKeep Windows for now because the Wi-Fi chip has no driver.\n", encoding="utf-8")
    c._last_detect.clear()
    x = item(run(c.view_fresh()), "win_ready")
    assert x["status"] == "fail" and x["detail"].startswith("Keep Windows")
    c.set_result("win_ready", "todo")
    run(c.run_one("win_ready"))
    report.write_text("Ready to replace Windows.\n", encoding="utf-8")
    import os
    os.utime(report, (time.time() + 5, time.time() + 5))
    c._last_detect.clear()
    assert item(run(c.view_fresh()), "win_ready")["status"] == "pass"


def test_an_old_report_without_the_button_never_fails_anything(tmp_path):
    c = _win_tc(tmp_path)
    (c.desktop_dirs[0] / auto.READY_REPORT).write_text("Keep Windows for now because x.\n", encoding="utf-8")
    import os
    os.utime(c.desktop_dirs[0] / auto.READY_REPORT, (time.time() + 5, time.time() + 5))
    assert item(run(c.view_fresh()), "win_ready")["status"] == "todo"     # only your own run can fail it


# ------------------------------------------------------------------ opening the window, never approving
def test_window_open_runs_only_the_checks_that_havent_passed(tc):
    for it in tc._auto_items():
        it.check = quick_pass
    tc._set("daemon_up", "pass", "earlier")

    async def go():
        tc.run_auto(fresh=False)
        await wait_done(tc)
    run(go())
    assert tc._result("daemon_up")["detail"] == "earlier"
    assert tc._result("key_works")["status"] == "pass"


def test_http_run_auto_on_open(tc):
    for it in tc._auto_items():
        it.check = quick_pass
    tc._set("daemon_up", "pass", "earlier")
    app = FastAPI()
    app.include_router(test_center.make_router(lambda: tc.daemon))
    with TestClient(app) as c:
        c.post("/tests/run-auto", json={"fresh": False})
        for _ in range(200):
            if not c.get("/tests").json()["running"]:
                break
            time.sleep(0.02)
        v = c.get("/tests").json()
    assert item(v, "daemon_up")["detail"] == "earlier" and item(v, "key_works")["status"] == "pass"
    assert "all_done" in v


def test_no_action_ever_approves_or_sends(tc, tmp_path, monkeypatch):
    import orion_layer
    monkeypatch.setattr(orion_layer, "history", lambda dd=None: [])
    tc.daemon.mission_control = FakeMissions()
    tc.daemon.projects = FakeProjects()
    tc.daemon.skills = FakeHub()
    tc.daemon.selfprog = FakeSelfProg(_banner_root(tmp_path))
    tc.daemon.privacy_dashboard = FakePrivacy()

    async def go():
        for it in tc.items():
            if it.action is not None:
                await tc.run_one(it.id)
        await asyncio.gather(*list(tc._bg))
        await tc.refresh()
    run(go())
    assert tc.daemon.board.verdicts == 0 and tc.daemon.comms.replies == 0
    assert not {"stage", "approve"} & set(tc.daemon.selfprog.calls)
    assert all(d["status"] == "pending" for d in tc.daemon.board.decisions.values())
