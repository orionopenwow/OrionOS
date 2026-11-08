"""OrionOS 1.0 gaps: Guide Mode, the wake word (openWakeWord + Wyoming), the one privacy dashboard and the voice
loopback check. No screen, microphone or model needed: every outside part is a fake."""
from __future__ import annotations

import asyncio
import json
import threading
from pathlib import Path

import numpy as np
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import guide_mode as gm
import hotmic as hm
import privacy_dashboard as pd
import voice_loopback as vl
import wake_word as ww

RATE, FRAME = hm.RATE, hm.FRAME


# ====================================================================== privacy dashboard
class Ev:
    def __init__(self, on=False):
        self.on = on

    def is_set(self):
        return self.on


class FakeCapture:
    def __init__(self):
        self.p = False

    def paused(self):
        return self.p

    def pause(self):
        self.p = True


class FakeMic:
    def __init__(self, listening=True, wake=True):
        self.listening = listening
        self.cfg = {"wake_word": wake}

    def config(self):
        return dict(self.cfg)

    def set_config(self, **kv):
        self.cfg.update(kv)

    def stop(self, remember=True):
        self.listening = False


class FakeSTT:
    def __init__(self):
        self._paused = Ev(False)

    def is_alive(self):
        return True

    def pause(self):
        self._paused.on = True


class FakeRecorder:
    recording = True

    def stop(self):
        self.recording = False
        return []


class FakeSkills:
    def __init__(self):
        self.recordings = {"r1": {"active": True}}

    async def stop_recording(self):
        self.recordings["r1"]["active"] = False
        return True


class FakePair:
    def __init__(self):
        self.mirror = True

    def settings(self):
        return {"inbox_mirror": self.mirror}

    def set_inbox_mirror(self, on):
        self.mirror = on


class FakeGuide:
    def __init__(self):
        self.on = True

    def active(self):
        return self.on

    async def stop(self, why=""):
        self.on = False


class FakeBus:
    def __init__(self):
        self.lines, self.events = [], []

    async def log(self, line, level="info"):
        self.lines.append(line)

    async def publish(self, ev, record=True):
        self.events.append(ev)


class FakeDaemon:
    def __init__(self):
        self.agents = {"system_capturer": True, "proactive_observer": True}
        self.capture = FakeCapture()
        self.hotmic = FakeMic()
        self.stt = FakeSTT()
        self.ext = type("E", (), {"recorder": FakeRecorder()})()
        self.skills = FakeSkills()
        self.phone_pair = FakePair()
        self.guide = FakeGuide()
        self.bus = FakeBus()

    async def set_agent(self, agent, enabled):
        self.agents[agent] = enabled
        if agent == "system_capturer" and not enabled:
            self.capture.pause()


def dash_client(d):
    app = FastAPI()
    app.include_router(pd.make_router(lambda: d))
    return TestClient(app)


def test_dashboard_lists_every_sensor_and_reads_real_state(monkeypatch):
    monkeypatch.setattr(pd, "camera_state", lambda path=pd.CAMERA_STATE: None)
    d = FakeDaemon()
    snap = dash_client(d).get("/privacy/dashboard").json()
    ids = [r["id"] for r in snap["sensors"]]
    for needed in ("screen", "hotmic", "wake", "camera", "recorder", "inbox_mirror", "telemetry", "crash_reports",
                   "guide", "voice_typing"):
        assert needed in ids
    by = {r["id"]: r for r in snap["sensors"]}
    assert by["screen"]["on"] and by["hotmic"]["on"] and by["inbox_mirror"]["on"] and by["recorder"]["on"]
    assert not by["telemetry"]["on"] and not by["telemetry"]["can_off"] and by["telemetry"]["available"]
    assert not by["camera"]["available"]                      # not OrionOS: no camera switch here
    assert snap["on"] >= 7 and "Screen reading" in snap["words"]


def test_one_tap_off_and_turn_everything_off(monkeypatch):
    monkeypatch.setattr(pd, "camera_state", lambda path=pd.CAMERA_STATE: None)
    d = FakeDaemon()
    c = dash_client(d)
    r = c.post("/privacy/off", json={"id": "hotmic"}).json()
    assert r["ok"] and not d.hotmic.listening and "Hot mic is off" in r["message"]
    assert any("Hot mic turned off" in ln for ln in d.bus.lines)
    assert c.post("/privacy/off", json={"id": "nope"}).status_code == 404
    r = c.post("/privacy/off-all").json()
    assert r["ok"] and r["on"] == 0 and r["words"] == "Nothing is watching or listening."
    assert d.agents == {"system_capturer": False, "proactive_observer": False} and d.capture.paused()
    assert not d.hotmic.cfg["wake_word"] and d.stt._paused.on and not d.ext.recorder.recording
    assert not d.skills.recordings["r1"]["active"] and not d.phone_pair.mirror and not d.guide.on


def test_a_broken_module_shows_unavailable_and_a_failing_off_is_reported(monkeypatch):
    monkeypatch.setattr(pd, "camera_state", lambda path=pd.CAMERA_STATE: True)

    def boom(_d):
        raise RuntimeError("Turn the camera off in Settings > Privacy.")
    monkeypatch.setattr(pd.BY_ID["camera"], "off", boom)
    d = FakeDaemon()
    d.phone_pair = None
    dash = pd.PrivacyDashboard(d)
    rows = {r["id"]: r for r in dash.snapshot()["sensors"]}
    assert rows["inbox_mirror"]["available"] is False and rows["camera"]["on"]
    res = asyncio.run(dash.off_all())
    assert not res["ok"] and "Settings > Privacy" in res["message"]


def test_camera_state_reads_the_orionos_switch(tmp_path, monkeypatch):
    monkeypatch.setattr(pd.sys, "platform", "linux")
    f = tmp_path / "camera"
    assert pd.camera_state(f) is False         # no file yet: OrionOS starts with it off
    f.write_text("on\n")
    assert pd.camera_state(f) is True
    assert pd.camera_state(tmp_path / "nope" / "camera") is None


# ====================================================================== wake word
def tone(seconds, amp, freq=220):
    t = np.arange(int(seconds * RATE)) / RATE
    return (amp * 32767 * np.sin(2 * np.pi * freq * t)).astype(np.int16)


def frames(audio):
    return [audio[i:i + FRAME] for i in range(0, len(audio) - FRAME + 1, FRAME)]


def speechlike(f):
    return hm.frame_db(f) > -60


class LoudDetector:
    """Stand-in for openWakeWord: 'hears Orion' whenever the 80 ms chunk is loud."""
    def __init__(self, threshold_db=-30):
        self.t = threshold_db
        self.calls = 0

    def __call__(self, chunk):
        self.calls += 1
        return 0.9 if hm.frame_db(chunk) > self.t else 0.0


def test_gate_arms_after_the_wake_word_and_disarms():
    now = [0.0]
    woke = []
    g = ww.WakeGate(LoudDetector(), 0.5, armed_s=5, clock=lambda: now[0], on_wake=woke.append)
    for f in frames(tone(0.3, 0.001)):
        g.feed(f)
    assert not g.armed() and not woke
    for f in frames(tone(0.3, 0.2)):
        g.feed(f)
    assert g.armed() and len(woke) == 1
    now[0] = 6
    assert not g.armed()
    g.until = 99
    g.disarm()
    assert not g.armed()


def test_make_gate_is_off_by_default_and_says_why(tmp_path, monkeypatch):
    assert ww.make_gate(tmp_path, {})[0] is None
    monkeypatch.setattr(ww, "why_not", lambda d: "openWakeWord isn't installed")
    g, why = ww.make_gate(tmp_path, {"wake_word": True})
    assert g is None and "openWakeWord" in why
    g, why = ww.make_gate(tmp_path, {"wake_word": True, "wake_threshold": 0.7}, detector=LoudDetector())
    assert g is not None and why == "" and g.threshold == 0.7


def test_find_model(tmp_path):
    assert ww.find_model(tmp_path, {}) is None
    (tmp_path / "models" / "wake").mkdir(parents=True)
    (tmp_path / "models" / "wake" / "orion.onnx").write_bytes(b"x")
    assert ww.find_model(tmp_path, {}).name == "orion.onnx"
    assert ww.find_model(tmp_path, {"ORION_WAKE_MODEL": str(tmp_path / "missing.onnx")}) is None


def test_hotmic_with_wake_word_runs_whisper_only_after_orion(tmp_path):
    me = np.array([1.0, 0.2, 0.0, 0.1], np.float32)
    hm.VoiceProfile(tmp_path / "voice_profile.json").save([me] * 4, "test")
    heard_calls, handled, events = [], [], []

    def transcribe(a):
        heard_calls.append(1)
        return "start the server"          # no "Orion" in the text: the wake model heard it instead
    det = LoudDetector(threshold_db=-12)   # only the loud phrase "contains" the wake word
    mic = hm.HotMic(tmp_path, handle=lambda t: handled.append(t), speak=lambda t: None, publish=events.append,
                    transcribe=transcribe, embed=lambda a: me, is_speech=speechlike, wake_detector=det)
    mic.set_config(min_db=-40, max_db=-3, wake_word=True)
    quiet_phrase = frames(np.concatenate([np.zeros(RATE // 2, np.int16), tone(1.2, 0.05), np.zeros(RATE, np.int16)]))
    assert mic.process_frames(quiet_phrase) == [] and heard_calls == []          # no wake word: Whisper never ran
    assert mic.last["ignored"].startswith("no wake word")
    loud_phrase = frames(np.concatenate([np.zeros(RATE // 2, np.int16), tone(1.2, 0.5), np.zeros(RATE, np.int16)]))
    assert mic.process_frames(loud_phrase) == ["start the server"] and handled == ["start the server"]
    assert any(e.get("event") == "HOTMIC_WAKE" for e in events)
    st = mic.status()
    assert st["wake"]["on"] and st["wake"]["active"]
    # the voice check still applies after the wake word
    stranger = hm.HotMic(tmp_path, handle=handled.append, speak=lambda t: None, transcribe=transcribe,
                         embed=lambda a: np.array([0.0, 0.1, 1.0, -0.4], np.float32), is_speech=speechlike,
                         wake_detector=LoudDetector(threshold_db=-12))
    assert stranger.process_frames(loud_phrase) == [] and stranger.last["ignored"] == "not your voice"


def test_hotmic_falls_back_to_the_whisper_text_check_when_the_model_is_missing(tmp_path, monkeypatch):
    me = np.array([1.0, 0.2, 0.0, 0.1], np.float32)
    hm.VoiceProfile(tmp_path / "voice_profile.json").save([me] * 4, "test")
    monkeypatch.setattr(ww, "why_not", lambda d: "the “orion” wake model isn't in ~/.orion/models/wake yet")
    mic = hm.HotMic(tmp_path, handle=lambda t: None, speak=lambda t: None, transcribe=lambda a: "Orion, open Discord",
                    embed=lambda a: me, is_speech=speechlike)
    mic.set_config(min_db=-40, max_db=-3, wake_word=True)
    ph = frames(np.concatenate([np.zeros(RATE // 2, np.int16), tone(1.2, 0.1), np.zeros(RATE, np.int16)]))
    assert mic.process_frames(ph) == ["open Discord"]
    assert mic.status()["wake"] == {"on": True, "active": False, "why": mic.wake_why} and "wake model" in mic.wake_why


def test_wyoming_wake_service_over_a_real_socket():
    async def go():
        srv = ww.WyomingWakeServer(lambda: LoudDetector(), port=0)
        port = await srv.start()
        assert srv.running
        r, w = await asyncio.open_connection("127.0.0.1", port)
        w.write(ww.encode_event("describe"))
        etype, data, _ = await ww.read_event(r)
        assert etype == "info" and data["wake"][0]["models"][0]["phrase"] == "Orion"
        fmt = {"rate": 16000, "width": 2, "channels": 1}
        w.write(ww.encode_event("audio-start", fmt))
        w.write(ww.encode_event("audio-chunk", fmt, tone(0.2, 0.001).tobytes()))
        w.write(ww.encode_event("audio-stop", {}))
        etype, _, _ = await ww.read_event(r)
        assert etype == "not-detected"
        w.write(ww.encode_event("audio-start", fmt))
        w.write(ww.encode_event("audio-chunk", {**fmt, "timestamp": 5}, tone(0.2, 0.3).tobytes()))
        etype, data, _ = await ww.read_event(r)
        assert etype == "detection" and data["name"] == "orion" and data["timestamp"] == 5
        w.close()
        await srv.stop()
        assert not srv.running
        assert srv.server is None
    asyncio.run(go())


def test_wyoming_only_listens_on_this_computer():
    assert ww.WYOMING_HOST == "127.0.0.1"


def test_wake_routes_turn_it_on_and_off(tmp_path):
    mic = hm.HotMic(tmp_path, handle=lambda t: None, speak=lambda t: None)
    d = type("D", (), {"hotmic": mic})()
    app = FastAPI()
    app.include_router(ww.make_router(lambda: d))
    c = TestClient(app)
    assert c.get("/wake").json()["on"] is False                       # off by default
    st = c.post("/wake", json={"on": True, "threshold": 2, "wyoming_port": 80}).json()
    assert st["on"] and mic.config()["wake_threshold"] == 0.95 and mic.config()["wyoming_port"] == 0
    assert not st["wyoming"]["running"]
    assert c.post("/wake", json={"on": False}).json()["on"] is False


# ====================================================================== guide mode
class FakeScreen(gm.Screen):
    def __init__(self):
        self.title_now = "Orion"
        self.words: list[dict] = []
        self.at = None
        self.withhold = ""
        super().__init__(grab=lambda: object(), ocr=lambda img: self.words, title=lambda: self.title_now,
                         atspi=lambda label: self.at if label else None, skip=lambda t: self.withhold)


def word(text, x, y=10):
    return {"text": text, "x": x, "y": y, "w": 40, "h": 14}


def test_match_guide_fills_the_app_name_and_skips_on_windows():
    g = gm.match_guide("install discord", orionos=True)
    assert g["title"] == "Install Discord" and g["steps"][1]["text"].endswith("type Discord.")
    assert g["steps"][3]["done"] == {"window": "Discord"}
    assert gm.match_guide("install it", orionos=True) is None
    assert gm.match_guide("pair my phone", orionos=True)["id"] == "pair-phone"
    assert gm.match_guide("install discord", orionos=False) is None


def test_voice_phrases():
    assert gm.START_RE.match("Orion, show me how to install Discord?").group("task") == "install Discord"
    assert gm.START_RE.match("walk me through connecting to wifi").group("task") == "connecting to wifi"
    assert not gm.START_RE.match("how was your day")
    assert gm.NEXT_RE.match("next step") and gm.NEXT_RE.match("Orion, done.") and gm.STOP_RE.match("stop the guide")


def test_ocr_find_spells_out_multi_word_labels():
    words = [word("Click", 0), word("Pair", 100), word("my", 150), word("phone.", 200)]
    assert gm.ocr_find(words, "Pair my phone") == {"x": 100, "y": 10, "w": 140, "h": 14}
    assert gm.ocr_find(words, "Unpair") is None


def test_clean_plan_from_a_model_answer():
    steps = gm.clean_plan({"steps": [{"text": "Open the browser", "find": "Firefox"}, "Type your password",
                                     {"nope": 1}] + [{"text": f"s{i}"} for i in range(20)]})
    assert steps[0] == {"text": "Open the browser", "find": "Firefox", "done": {"gone": True}}
    assert "doesn't read or type" in steps[1]["text"] and len(steps) == gm.MAX_STEPS


def run(coro):
    return asyncio.run(coro)


def test_guide_rings_the_button_and_advances_when_the_user_does_the_step(tmp_path):
    async def go():
        scr = FakeScreen()
        events = []

        async def pub(e):
            events.append(e)
        g = gm.GuideMode(screen=scr, publish=pub, runtime_dir=tmp_path, poll_s=999, overlay=lambda: None)
        st = await g.start("pair my phone")
        assert st["title"] == "Pair your phone" and st["index"] == 0 and st["auto"]
        assert json.loads((tmp_path / gm.STATE_FILE).read_text())["active"]
        scr.title_now = "Orion Settings"                         # the user opened Settings
        assert await g.tick() and g.i == 1
        scr.at = {"x": 5, "y": 300, "w": 80, "h": 24}             # AT-SPI found "Orion" in the list
        await g.tick()
        assert g.state()["target"] == scr.at and g.state()["source"] == "accessibility"
        scr.at, scr.words = None, [word("Pair", 400, 50), word("my", 450, 50), word("phone", 500, 50)]
        assert await g.tick() and g.i == 2                         # "pair my phone" now on screen
        assert g.state()["target"] == {"x": 400, "y": 50, "w": 140, "h": 14} and g.state()["source"] == "screen"
        await g.next()                                             # Done button
        assert g.i == 3 and not g.state()["auto"]
        assert not await g.tick()                                  # manual step: only Done moves on
        res = await g.next()
        assert res == {"active": False, "finished": "Pair your phone"}
        assert not (tmp_path / gm.STATE_FILE).exists()
        assert any(e["guide"].get("why", "").startswith("finished") for e in events)
    run(go())


def test_guide_reads_nothing_on_private_screens(tmp_path):
    async def go():
        scr = FakeScreen()
        scr.withhold = "a password manager"
        scr.at = {"x": 1, "y": 1, "w": 1, "h": 1}
        g = gm.GuideMode(screen=scr, runtime_dir=tmp_path, poll_s=999, overlay=lambda: None)
        st = await g.start("pair my phone")
        assert st["target"] is None and "password manager" in st["withheld"]
        scr.title_now = "Settings"
        assert not await g.tick()                                  # can't see it: waits for Done
        await g.stop()
    run(go())


def test_gone_step_and_model_planned_guide(tmp_path):
    async def plan(task):
        return {"steps": [{"text": "Click Start", "find": "Start"}, {"text": "All set"}]}

    async def go():
        scr = FakeScreen()
        scr.words = [word("Start", 10)]
        g = gm.GuideMode(screen=scr, planner=plan, runtime_dir=tmp_path, poll_s=999, overlay=lambda: None)
        st = await g.start("defragment my thoughts")
        assert st["id"] == "made" and st["target"]
        scr.words = []
        assert not await g.tick()                                  # gone once: not yet
        assert await g.tick() and g.i == 1                         # gone twice: done
        await g.stop()

        async def none(task):
            return None
        g2 = gm.GuideMode(screen=scr, planner=none, runtime_dir=tmp_path, overlay=lambda: None)
        with pytest.raises(LookupError):
            await g2.start("defragment my thoughts")
    run(go())


def test_phone_card_done_moves_the_guide_on(tmp_path):
    class Cards:
        def __init__(self):
            self.made, self.st = [], {}

        def create(self, text, target=None):
            cid = f"{len(self.made):016x}"
            self.made.append((text, target))
            self.st[cid] = "open"
            return {"id": cid}

        def get(self, cid):
            return {"state": self.st[cid]}

    async def go():
        cards = Cards()
        d = type("D", (), {"cards": cards, "bus": None})()
        g = gm.GuideMode(d, screen=FakeScreen(), runtime_dir=tmp_path, poll_s=999, overlay=lambda: None)
        await g.start("take a screenshot", send_to_phone=True)
        assert cards.made[0][0].startswith("Press Print Screen")
        cards.st[f"{0:016x}"] = "done"
        assert await g.tick() and g.i == 1 and len(cards.made) == 2
        await g.stop()
    run(go())


def test_chat_and_routes(tmp_path, monkeypatch):
    monkeypatch.setattr(gm.sys, "platform", "linux")

    class D:
        bus = None
        orchestrator = None

    d = D()
    d.guide = gm.GuideMode(d, screen=FakeScreen(), runtime_dir=tmp_path, poll_s=999, overlay=lambda: None)

    async def talk():
        assert await gm.chat(d, "what's the weather") is None
        out = await gm.chat(d, "Orion, show me how to take a screenshot")
        assert out.startswith("Take a screenshot, 2 steps. Step 1:")
        assert (await gm.chat(d, "next step")).startswith("Draw on it")
        assert (await gm.chat(d, "go back")).startswith("Press Print Screen")
        assert await gm.chat(d, "stop the guide") == "Guide stopped."
        assert "no AI model" in await gm.chat(d, "show me how to juggle")
    run(talk())

    app = FastAPI()
    app.include_router(gm.make_router(lambda: d))
    c = TestClient(app)
    assert c.get("/guide").json() == {"active": False}
    assert any(x["id"] == "wifi" for x in c.get("/guide/list").json()["guides"])
    assert c.post("/guide/start", json={"task": ""}).status_code == 400
    assert c.post("/guide/start", json={"task": "juggle"}).status_code == 404
    st = c.post("/guide/start", json={"task": "connect to wifi"}).json()
    assert st["title"] == "Connect to Wi-Fi"
    assert c.post("/guide/next").json()["index"] == 1
    assert c.post("/guide/back").json()["index"] == 0
    assert c.post("/guide/stop").json() == {"active": False}


# ====================================================================== voice loopback
def test_loopback_score():
    assert vl.score(vl.PHRASE, "Orion voice check. 7 blue stars over the quiet desert.") == 1.0
    assert vl.score(vl.PHRASE, "O'Ryan voice check seven blue stars") >= 0.5
    assert vl.score(vl.PHRASE, "thank you") < vl.PASS_SCORE


def test_loopback_check_with_a_fake_whisper(tmp_path):
    ok = vl.check("x.wav", transcribe=lambda p: "orion voice check seven blue stars over the quiet desert")
    assert ok["ok"] and not ok["skipped"]
    bad = vl.check("x.wav", transcribe=lambda p: "")
    assert not bad["ok"] and bad["why"]

    def boom(p):
        raise OSError("no file")
    assert not vl.check("x.wav", transcribe=boom)["ok"]
    assert vl.main(["phrase"]) == 0 and vl.main([]) == 2


def test_accessibility_box_to_screen_pixels():
    # a window at (1920+100, 50) on the right-hand screen (scale 2), button 10,20 inside it
    box = gm.to_output_pixels({"x": 10, "y": 20, "w": 30, "h": 10}, {"x": 2020, "y": 50},
                              {"x": 1920, "y": 0}, 2.0)
    assert box == {"x": 220, "y": 140, "w": 60, "h": 20}


# ====================================================================== wired into the real daemon
def test_routes_are_on_the_real_daemon_and_need_the_token():
    import main
    import module_guard
    from conftest import LOCAL_BASE, auth_headers, daemon_client

    assert not any(n in module_guard._failed for n in ("guide_mode", "wake_word", "privacy_dashboard"))
    assert TestClient(main.app, base_url=LOCAL_BASE).get("/privacy/dashboard").status_code == 401
    with daemon_client(main.app) as c:
        snap = c.get("/privacy/dashboard", headers=auth_headers()).json()
        by = {s["id"]: s for s in snap["sensors"]}
        assert not by["hotmic"]["on"] and not by["wake"]["on"] and not by["screen"]["on"]   # all off by default
        assert c.get("/guide", headers=auth_headers()).json() == {"active": False}
        assert c.get("/wake", headers=auth_headers()).json()["on"] is False
