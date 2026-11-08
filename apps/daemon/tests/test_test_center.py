"""Orion Test Center (test_center.py, phone_tests.py): the checklist, automatic checks against fake Orion parts, the
stand-in agent mission in a throwaway repo, results that persist, the shareable report, the HTTP and phone routes.
Nothing here touches the network or a real agent."""
from __future__ import annotations

import asyncio
import json
import shutil
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

import test_center
from test_center import TEST_CARD_TASK, TestCenter

ITEM_KEYS = {"id", "title", "where", "kind", "status", "detail", "next"}


# ------------------------------------------------------------------ fake Orion parts
class FakeBus:
    def __init__(self, kinds=("desktop",)) -> None:
        self.kinds = list(kinds)
        self.events: list[dict] = []

    def clients_state(self):
        return [{"id": str(i), "kind": k} for i, k in enumerate(self.kinds)]

    async def publish(self, event, *, audience="ui", record=True):
        self.events.append(event)


class FakeStore:
    def __init__(self, phones=None) -> None:
        self.kv = {"phones": json.dumps(phones or {})}

    def get(self, k, default=None):
        return self.kv.get(k, default)

    def x(self, *a, **k):
        return None


class FakeBoard:
    def __init__(self, phones=None) -> None:
        self.store = FakeStore(phones)
        self.executors: dict = {}
        self.decisions: dict = {}
        self.verdicts = 0

    async def propose(self, task_type, summary, details, proposer, risk="low", simulated=True):
        d = {"id": f"dec-{len(self.decisions) + 1}", "task_type": task_type, "status": "pending",
             "summary": summary}
        self.decisions[d["id"]] = d
        return d

    def get(self, did):
        return self.decisions.get(did)

    async def verdict(self, *a, **k):   # the Test Center must never call this
        self.verdicts += 1


class FakeHotMic:
    listening = False

    def config(self):
        return {"enabled": False, "wake_word": False}

    def missing(self):
        return []


class FakeComms:
    def __init__(self) -> None:
        self.replies = 0

    async def reply(self, key, text):
        self.replies += 1
        return True, "sent", None


class FakePair:
    def __init__(self, comms=None, sneaky=False) -> None:
        self.comms, self.sneaky = comms, sneaky

    async def draft(self, app, sender, text, thread=None):
        if self.sneaky:
            await self.comms.reply("x", "sent behind your back")
        return "Yes, still on for tomorrow."

    def _health(self):
        return {"level": 0, "text": "Everything looks fine."}


class FakeStt:
    def is_alive(self):
        return True


def phone(last_ts_ms: int, name="Testphone Zed"):
    return {"dev1": {"name": name, "fp": "AB:CD", "added": 1, "last_ts": last_ts_ms, "read": "", "sign": ""}}


def make_daemon(phones=None, comms=None, sneaky=False, **kw):
    comms = comms or FakeComms()
    d = SimpleNamespace(bus=FakeBus(), board=FakeBoard(phones), hotmic=FakeHotMic(), stt=FakeStt(), comms=comms,
                        phone_pair=FakePair(comms, sneaky), started_at=time.time() - 120, spoken=[])

    async def speak_and_stream(text):
        d.spoken.append(text)
    d.speak_and_stream = speak_and_stream
    for k, v in kw.items():
        setattr(d, k, v)
    return d


async def quick_pass(_tc):
    return "pass", "fine"


def which_none(_b):
    return None


@pytest.fixture
def tc(tmp_path):
    d = make_daemon(phones=phone(int(time.time() * 1000)))
    c = TestCenter(d, tmp_path, platform="other", which=lambda b: f"/usr/bin/{b}" if b == "claude" else None)
    d.test_center = c
    return c


def run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


async def wait_done(c: TestCenter, timeout=60):
    t0 = time.monotonic()
    while c.running() and time.monotonic() - t0 < timeout:
        await asyncio.sleep(0.05)


def item(view, iid):
    for g in view["groups"]:
        for x in g["items"]:
            if x["id"] == iid:
                return x
    return None


# ------------------------------------------------------------------ the checklist
def test_view_shape_and_contract(tc):
    v = tc.view()
    assert set(v) >= {"groups", "next", "summary"}
    assert set(v["summary"]) == {"pass", "fail", "todo", "skip"}
    ids = set()
    for g in v["groups"]:
        assert {"id", "title", "items"} <= set(g)
        for x in g["items"]:
            assert ITEM_KEYS <= set(x)
            assert x["where"] in ("pc", "phone", "both") and x["kind"] in ("auto", "manual")
            assert x["status"] == "todo"
            assert x["next"].endswith(".") and "\n" not in x["next"]
            ids.add(x["id"])
    for must in ("daemon_up", "key_works", "hud_connected", "health_light", "core_renders", "glass_windows",
                 "mic_found", "tts_speaks", "mic_off_default", "phone_paired", "phone_reaches", "approve_card",
                 "inbox_draft", "send_work", "pocket_orion", "agents_found", "dry_mission", "real_mission",
                 "start_project", "record_skill", "replay_skill", "freeze_phone", "unfreeze_phone", "undo_last",
                 "privacy_off", "ghost_preview"):
        assert must in ids, must
    # first thing to do: an automatic check
    assert v["next"]["kind"] == "auto"
    assert v["summary"]["todo"] == sum(len(g["items"]) for g in v["groups"])


def test_os_and_windows_items_only_where_they_apply(tmp_path):
    d = make_daemon()
    other = {x["id"] for g in TestCenter(d, tmp_path / "a", platform="other").view()["groups"] for x in g["items"]}
    assert not any(i.startswith(("os_", "win_")) for i in other)
    win = TestCenter(d, tmp_path / "b", platform="windows").view()
    assert any(g["id"] == "windows" for g in win["groups"]) and not any(g["id"] == "orionos" for g in win["groups"])
    os_stick = TestCenter(d, tmp_path / "c", platform="orionos", profile="image", which=which_none).view()
    os_ids = {x["id"] for g in os_stick["groups"] for x in g["items"]}
    assert {"os_usb_boot", "os_vault", "os_wifi", "os_sleep_lock"} <= os_ids
    assert "os_restart_windows" not in os_ids            # no Windows next to it
    assert not any(i.startswith("win_") for i in os_ids)
    laptop = TestCenter(d, tmp_path / "d", platform="orionos", profile="baremetal",
                        which=lambda b: "/usr/local/bin/x" if b == "orion-restart-windows" else None).view()
    lap_ids = {x["id"] for g in laptop["groups"] for x in g["items"]}
    assert "os_restart_windows" in lap_ids and "os_usb_boot" not in lap_ids


# ------------------------------------------------------------------ automatic checks
def test_run_auto_against_fake_parts(tc, monkeypatch):
    tc.by_id["dry_mission"].check = quick_pass          # the real one has its own test below
    tc.set_result("core_renders", "pass", "Quality: High.", by="orion-window")   # the window posts this first
    import api_token
    monkeypatch.setattr(api_token, "read_local", lambda: api_token.get())

    async def go():
        v = tc.run_auto()
        assert v["running"] is True
        assert item(v, "daemon_up")["status"] == "running"
        await wait_done(tc)
        return tc.view()
    v = run(go())
    for iid in ("daemon_up", "key_works", "hud_connected", "health_light", "mic_found", "mic_off_default",
                "phone_paired", "phone_reaches", "inbox_draft", "agents_found"):
        assert item(v, iid)["status"] == "pass", (iid, item(v, iid))
    assert "Claude Code" in item(v, "agents_found")["detail"]
    assert item(v, "real_mission")["status"] == "todo"        # Claude Code is installed: stays manual
    assert item(v, "core_renders")["status"] == "pass"        # kept: run-all doesn't overwrite the window's result
    assert v["next"]["kind"] == "manual"
    assert tc.daemon.board.verdicts == 0


def test_failures_give_one_plain_next_step(tmp_path, monkeypatch):
    d = make_daemon(phones=phone(int((time.time() - 3600) * 1000)))
    d.bus.kinds = ["extension"]
    d.stt = None
    c = TestCenter(d, tmp_path, platform="other", which=which_none)
    c.by_id["dry_mission"].check = quick_pass
    import api_token
    monkeypatch.setattr(api_token, "read_local", lambda: "")

    async def go():
        c.run_auto()
        await wait_done(c)
        return c.view()
    v = run(go())
    for iid in ("key_works", "hud_connected", "mic_found", "phone_reaches", "agents_found"):
        x = item(v, iid)
        assert x["status"] == "fail", iid
        assert x["next"] and x["next"] != "Nothing more to do here."
    assert item(v, "real_mission")["status"] == "skip"         # no Claude Code: nothing to try
    assert v["summary"]["fail"] >= 5


def test_draft_check_fails_if_anything_is_sent(tmp_path):
    comms = FakeComms()
    d = make_daemon(comms=comms, sneaky=True)
    c = TestCenter(d, tmp_path, platform="other")
    run(c._run_check(c.by_id["inbox_draft"]))
    assert c._result("inbox_draft")["status"] == "fail"
    assert comms.replies == 0                                  # the spy blocked it; the real reply never ran
    assert d.comms.reply.__name__ == "reply"                   # and the hub is put back


def test_hot_mic_turned_on_by_user_still_passes(tc):
    tc.daemon.hotmic.listening = True
    run(tc._run_check(tc.by_id["mic_off_default"]))
    r = tc._result("mic_off_default")
    assert r["status"] == "pass" and "you turned it on" in r["detail"]


@pytest.mark.skipif(sys.platform == "win32" or not shutil.which("git"), reason="needs git (POSIX test run)")
def test_dry_run_mission_makes_a_review_card_and_cleans_up(tc, tmp_path, monkeypatch):
    scratch = tmp_path / "tmp"
    scratch.mkdir()
    monkeypatch.setattr(test_center.tempfile, "tempdir", str(scratch))
    monkeypatch.setenv("ORION_AGENT_WORK_DIR", str(tmp_path / "shared"))   # must not be used
    run(tc._run_check(tc.by_id["dry_mission"]))
    r = tc._result("dry_mission")
    assert r["status"] == "pass", r
    assert "review card" in r["detail"]
    assert list(scratch.iterdir()) == []                       # throwaway repo deleted
    assert not (tmp_path / "shared").exists()
    assert str(scratch) not in r["detail"]
    assert tc.daemon.board.verdicts == 0


def test_dry_run_mission_skips_when_frozen(tc, monkeypatch):
    import freeze
    monkeypatch.setattr(freeze, "is_frozen", lambda: True)
    run(tc._run_check(tc.by_id["dry_mission"]))
    assert tc._result("dry_mission")["status"] == "skip"


# ------------------------------------------------------------------ manual items and helpers
def test_test_card_is_sent_never_approved_and_seen_when_you_approve(tc):
    out = run(tc.run_one("approve_card"))
    assert out["status"] == "todo" and "Sent" in out["detail"]
    board = tc.daemon.board
    (dec,) = board.decisions.values()
    assert dec["task_type"] == TEST_CARD_TASK and board.verdicts == 0
    assert TEST_CARD_TASK in board.executors
    assert board.executors[TEST_CARD_TASK](dec)["test"] is True
    import boardroom
    assert boardroom.TASK_TYPES[TEST_CARD_TASK].max_level <= 1    # never runs by itself
    assert item(tc.view(), "approve_card")["status"] == "todo"
    dec["status"] = "approved"                                     # you approved it on the phone
    assert item(tc.view(), "approve_card")["status"] == "pass"


def test_say_something_speaks_and_waits_for_you(tc):
    out = run(tc.run_one("tts_speaks"))
    assert tc.daemon.spoken and out["status"] == "todo"


def test_open_helpers_broadcast_to_the_window(tc):
    run(tc.run_one("start_project"))
    assert any(e.get("event") == "HUD_OPEN" and e.get("what") == "projects" for e in tc.daemon.bus.events)


def test_freeze_and_phone_missions_are_seen(tc, monkeypatch):
    import freeze
    st = {"frozen": True, "by": "phone: Testphone Zed"}
    monkeypatch.setattr(freeze, "state", lambda: dict(st))
    v = tc.view()
    assert item(v, "freeze_phone")["status"] == "pass"
    assert "Zed" not in item(v, "freeze_phone")["detail"]
    st["frozen"] = False
    assert item(tc.view(), "unfreeze_phone")["status"] == "pass"
    tc.daemon.mission_control = SimpleNamespace(list=lambda: [{"source": "phone", "created": time.time() + 1}])
    assert item(tc.view(), "send_work")["status"] == "pass"


def test_results_persist_and_reset(tc, tmp_path):
    tc.set_result("glass_windows", "pass", "looks lovely")
    tc.set_result("pocket_orion", "skip")
    with pytest.raises(ValueError):
        tc.set_result("glass_windows", "great")
    with pytest.raises(KeyError):
        tc.set_result("nope", "pass")
    again = TestCenter(tc.daemon, tmp_path, platform="other")
    v = again.view()
    assert item(v, "glass_windows")["status"] == "pass" and item(v, "glass_windows")["note"] == "looks lovely"
    assert item(v, "pocket_orion")["status"] == "skip"
    assert v["summary"]["pass"] >= 1 and v["summary"]["skip"] >= 1
    again.reset()
    assert TestCenter(tc.daemon, tmp_path, platform="other").view()["summary"]["pass"] == 0


def test_running_left_over_after_a_restart_goes_back_to_todo(tc, tmp_path):
    tc._set("daemon_up", "running")
    tc.save()
    assert item(TestCenter(tc.daemon, tmp_path, platform="other").view(), "daemon_up")["status"] == "todo"


def test_window_measured_result_becomes_the_detail(tc):
    tc.set_result("core_renders", "pass", "Quality: High. Graphics: Intel Iris Xe.", by="orion-window")
    x = item(tc.view(), "core_renders")
    assert x["status"] == "pass" and x["detail"].startswith("Quality: High")


# ------------------------------------------------------------------ report
def test_report_has_no_secrets_or_names(tc):
    secret = "sk-" + "proj-" + "A1b2C3d4E5f6G7h8I9j0K1l2"
    tc.set_result("glass_windows", "fail", f"Testphone Zed showed {secret} oddly")
    tc.set_result("pocket_orion", "skip")
    md = tc.report()
    assert md.startswith("# Orion test report")
    assert secret not in md and "Testphone Zed" not in md
    assert "[!] Windows look like frosted glass" in md
    assert "[-] Pocket Orion" in md
    assert "didn't work" in md


# ------------------------------------------------------------------ voice
def test_voice_words():
    for s in ("Orion, test everything", "test everything", "Hey Orion run all the tests.", "open the test centre"):
        assert test_center.matches(s), s
    for s in ("test everything in my repo please now", "what's the weather", ""):
        assert not test_center.matches(s), s


def test_chat_opens_the_window_and_starts_checks(tc):
    async def go():
        reply = await test_center.chat(tc.daemon, "Orion, test everything")
        await wait_done(tc)
        return reply
    tc.by_id["dry_mission"].check = quick_pass
    reply = run(go())
    assert "Test Center" in reply
    assert any(e.get("event") == "HUD_OPEN" and e.get("what") == "tests" for e in tc.daemon.bus.events)
    assert tc.take_open_request() == {"open": True, "run": True}
    assert tc.take_open_request() == {"open": False, "run": False}
    assert run(test_center.chat(tc.daemon, "hello there")) is None


# ------------------------------------------------------------------ HTTP routes
def test_http_routes(tc):
    tc.by_id["dry_mission"].check = quick_pass
    app = FastAPI()
    app.include_router(test_center.make_router(lambda: tc.daemon))
    with TestClient(app) as c:
        v = c.get("/tests").json()
        assert set(v) >= {"groups", "next", "summary"}
        assert c.post("/tests/glass_windows/result", json={"status": "pass", "note": "ok"}).json()["item"]["status"] == "pass"
        assert c.post("/tests/glass_windows/result", json={"status": "maybe"}).status_code == 400
        assert c.post("/tests/nope/result", json={"status": "pass"}).status_code == 404
        assert c.post("/tests/nope/run").status_code == 404
        r = c.post("/tests/run-auto").json()
        assert r["summary"]["todo"] >= 1
        for _ in range(200):
            if not c.get("/tests").json()["running"]:
                break
            time.sleep(0.05)
        assert item(c.get("/tests").json(), "daemon_up")["status"] == "pass"
        assert c.post("/tests/daemon_up/run").json()["item"]["status"] in ("running", "pass")
        assert c.get("/tests/report").json()["markdown"].startswith("# Orion test report")
        assert c.post("/tests/open", json={"run": False}).json() == {"ok": True}
        assert c.get("/tests/open-request").json() == {"open": True, "run": False}
        assert c.get("/tests/open-request").json() == {"open": False, "run": False}
        assert c.post("/tests/reset").json()["summary"]["pass"] == 0


def test_phone_routes(tc):
    import phone_tests
    app = FastAPI()
    calls = []

    async def phone_auth(request, need, max_body=None):
        calls.append(need)
        if request.headers.get("x-test-deny"):
            return None, JSONResponse({"error": "unknown or unpaired phone"}, status_code=401)
        return {"device_id": "dev1", "name": "Testphone Zed", "fingerprint": "AB"}, None

    async def json_body(request):
        try:
            return await request.json()
        except Exception:
            return None
    tc.daemon.board.store.kv["phones"] = json.dumps({})        # nobody reached the PC yet
    phone_tests.hook(tc.daemon, app, phone_auth, json_body)
    with TestClient(app) as c:
        assert c.get("/phone/tests", headers={"x-test-deny": "1"}).status_code == 401
        v = c.get("/phone/tests").json()
        wheres = {x["where"] for g in v["groups"] for x in g["items"]}
        assert wheres <= {"phone", "both"} and v["next"] is not None
        assert v["next"]["where"] in ("phone", "both")
        assert item(v, "phone_reaches")["status"] == "pass"
        assert item(v, "glass_windows") is None
        r = c.post("/phone/tests/send_work/result",
                   json={"status": "pass", "facts": {"android": 15, "app_version": "0.9", "api_token": "x"}})
        assert r.json()["item"]["status"] == "pass"
        assert tc._result("send_work")["facts"] == {"android": 15, "app_version": "0.9"}
        assert c.post("/phone/tests/glass_windows/result", json={"status": "pass"}).status_code == 404
        assert c.post("/phone/tests/send_work/result", json={"status": "?"}).status_code == 400
    assert set(calls) == {"read"}                               # results never need (or give) the fingerprint


def test_phone_check_ids_exist_on_the_pc():
    """The phone's Test screen fills in PC checklist items by exact id (PhoneTests.kt MATCH); keep them in step."""
    import re
    from pathlib import Path
    kt = (Path(__file__).resolve().parents[3] / "apps/mobile/app/src/main/java/com/orion/mobile/PhoneTests.kt").read_text()
    block = kt[kt.index("private val MATCH"):kt.index(")\n", kt.index("private val MATCH"))]
    phone_ids = set(re.findall(r'listOf\("([a-z_]+)"\)', block))
    src = (Path(__file__).resolve().parents[1] / "test_center.py").read_text()
    pc_phone_ids = set(re.findall(r'I\("([a-z_]+)", "[a-z]+", "[^"]+", "(?:phone|both)"', src))
    assert phone_ids and phone_ids <= pc_phone_ids, phone_ids - pc_phone_ids


def test_report_scrub_keeps_orion_words_and_hides_real_names(monkeypatch):
    import getpass, socket
    import test_center as tc
    monkeypatch.setattr(getpass, "getuser", lambda: "orion")
    monkeypatch.setattr(socket, "gethostname", lambda: "Sams-Laptop")
    monkeypatch.setattr(tc, "_phones", lambda self: [])
    obj = type("X", (), {})()
    out = tc.TestCenter._scrub(obj, "orion-ctl voice enrol; orion-mako.service failed on Sams-Laptop")
    assert "orion-ctl" in out and "orion-mako.service" in out and "Sams-Laptop" not in out
