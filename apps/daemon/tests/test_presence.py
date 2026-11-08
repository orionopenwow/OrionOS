"""Phase 5: webcam presence and hand gestures, tested with made-up landmark sequences (no camera, no MediaPipe)."""
from __future__ import annotations

import asyncio
import gc
import json
import weakref

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import gestures as G
import presence as P


# ============================================================================================ synthetic hands
def hand(pose: str, cx: float = 0.5, cy: float = 0.5, s: float = 0.1) -> list[tuple[float, float, float]]:
    """An upright hand (fingers up) in MediaPipe order; s = wrist-to-middle-knuckle length."""
    pts = [(0.0, 0.0)] * 21
    pts[0] = (cx, cy + s)                                   # wrist
    mcps = {5: -0.3, 9: -0.1, 13: 0.1, 17: 0.3}
    out = {"open": "1111", "point": "1000", "two": "1100", "fist": "0000", "thumbs_up": "0000", "pinch": "1000"}[pose]
    for (mcp, dx), up in zip(mcps.items(), out):
        x = cx + dx * s
        pts[mcp] = (x, cy)
        if up == "1":
            pts[mcp + 1] = (x, cy - 0.3 * s)
            pts[mcp + 2] = (x, cy - 0.5 * s)
            pts[mcp + 3] = (x, cy - 0.7 * s)
        else:
            pts[mcp + 1] = (x, cy - 0.2 * s)
            pts[mcp + 2] = (x, cy - 0.1 * s)
            pts[mcp + 3] = (x, cy + 0.05 * s)
    pts[1] = (cx - 0.3 * s, cy + 0.8 * s)
    pts[2] = (cx - 0.45 * s, cy + 0.6 * s)
    if pose == "open":
        pts[3], pts[4] = (cx - 0.75 * s, cy + 0.35 * s), (cx - 1.0 * s, cy + 0.2 * s)
    elif pose == "thumbs_up":
        pts[3], pts[4] = (cx - 0.45 * s, cy - 0.3 * s), (cx - 0.45 * s, cy - 0.8 * s)
    elif pose == "pinch":
        pts[3] = (cx - 0.35 * s, cy - 0.3 * s)
        pts[4] = (pts[8][0] + 0.02 * s, pts[8][1] + 0.05 * s)
    else:
        pts[3], pts[4] = (cx - 0.3 * s, cy + 0.45 * s), (cx - 0.15 * s, cy + 0.3 * s)
    return [(x, y, 0.0) for x, y in pts]


def run(engine, frames, t0=0.0, dt=1 / 15):
    """frames: list of hand lists; returns all gestures with their times."""
    out = []
    for i, hands in enumerate(frames):
        t = t0 + i * dt
        for ev in engine.feed(t, hands):
            out.append((round(t, 2), ev))
    return out


def names(evs):
    return [e["gesture"] for _, e in evs]


def hold(pose, seconds, dt=1 / 15, **kw):
    return [[hand(pose, **kw)]] * int(seconds / dt)


# ============================================================================================ poses
@pytest.mark.parametrize("p", ["open", "point", "two", "fist", "thumbs_up", "pinch"])
def test_each_pose_is_recognised(p):
    assert G.pose(hand(p)) == p
    assert G.pose(hand(p, cx=0.3, cy=0.6, s=0.2)) == p      # any place, any size


def test_bad_input_is_harmless():
    assert G.pose([]) == "other" and G.pose(None) == "other"
    assert G.GestureEngine().feed(0, [[(0, 0)] * 5, None]) == []


# ============================================================================================ the gesture zone
def test_nothing_fires_before_half_a_second_in_the_zone():
    e = G.GestureEngine()
    evs = run(e, hold("point", 0.4))
    assert evs == []
    evs = run(G.GestureEngine(), hold("point", 0.7))
    assert evs and evs[0][0] >= 0.5 and names(evs)[0] == "pointer"


def test_hand_outside_the_zone_never_fires():
    e = G.GestureEngine(zone=(0.3, 0.3, 0.7, 0.7))
    assert run(e, hold("open", 3, cx=0.9, cy=0.5)) == []


# ============================================================================================ gestures
def test_open_palm_hold_is_stop_once():
    evs = run(G.GestureEngine(), hold("open", 3))
    assert names(evs) == ["stop"]
    assert evs[0][0] >= G.PALM_HOLD_S


def test_moving_palm_is_not_stop_but_a_swipe():
    frames = hold("open", 0.6) + [[hand("open", cx=0.4 + 0.04 * i)] for i in range(8)]
    evs = run(G.GestureEngine(), frames)
    assert "stop" not in names(evs) and names(evs) == ["swipe_right"]
    frames = hold("open", 0.6, cx=0.7) + [[hand("open", cx=0.7 - 0.04 * i)] for i in range(8)]
    assert names(run(G.GestureEngine(), frames)) == ["swipe_left"]


def test_point_then_pinch_clicks():
    frames = hold("point", 0.6) + hold("pinch", 0.3)
    evs = run(G.GestureEngine(), frames)
    assert names(evs).count("click") == 1
    click = next(e for _, e in evs if e["gesture"] == "click")
    assert 0 <= click["x"] <= 1 and 0 <= click["y"] <= 1


def test_draw_a_rectangle_makes_a_new_desktop_and_a_circle_does_not():
    import math
    corners = [(0.35, 0.3), (0.65, 0.3), (0.65, 0.6), (0.35, 0.6), (0.35, 0.3)]
    path = []
    for (x0, y0), (x1, y1) in zip(corners, corners[1:]):
        path += [(x0 + (x1 - x0) * k / 6, y0 + (y1 - y0) * k / 6) for k in range(6)]
    path.append(corners[-1])

    def draw(points):
        frames = hold("two", 0.6, cx=points[0][0], cy=points[0][1] + 0.07)
        for x, y in points:   # the index tip draws: move the hand so the tip is at (x, y)
            frames.append([hand("two", cx=x, cy=y + 0.07)])
        return names(run(G.GestureEngine(), frames))

    assert draw(path) == ["rectangle"]
    circle = [(0.5 + 0.15 * math.cos(a / 24 * 2 * math.pi), 0.45 + 0.15 * math.sin(a / 24 * 2 * math.pi))
              for a in range(25)]
    assert draw(circle) == []


def test_grab_and_throw():
    frames = hold("fist", 0.7) + [[hand("fist", cx=0.5 + 0.09 * i)] for i in range(4)] + [[hand("open", cx=0.85)]]
    evs = run(G.GestureEngine(), frames)
    assert [e for _, e in evs if e["gesture"] == "throw"] == [{"gesture": "throw", "direction": "right"}]
    frames = hold("fist", 0.7, cy=0.3) + [[hand("fist", cy=0.3 + 0.09 * i)] for i in range(4)] + [[hand("open", cy=0.62)]]
    assert [e["direction"] for _, e in run(G.GestureEngine(), frames) if e["gesture"] == "throw"] == ["down"]
    # opening the hand without moving puts it down: nothing happens
    frames = hold("fist", 1.0) + [[hand("open")]]
    assert "throw" not in names(run(G.GestureEngine(), frames))


def test_two_hands_spread_and_squeeze():
    def two(gap):
        return [hand("open", cx=0.5 - gap / 2, s=0.06), hand("open", cx=0.5 + gap / 2, s=0.06)]
    frames = [two(0.2)] * 10 + [two(0.2 + 0.02 * i) for i in range(10)]
    assert names(run(G.GestureEngine(), frames)) == ["spread"]
    frames = [two(0.5)] * 10 + [two(0.5 - 0.03 * i) for i in range(10)]
    assert names(run(G.GestureEngine(), frames)) == ["squeeze"]


def test_thumbs_up_needs_a_short_hold():
    assert names(run(G.GestureEngine(), hold("thumbs_up", 0.8))) == []
    assert names(run(G.GestureEngine(), hold("thumbs_up", 1.5))) == ["thumbs_up"]


# ============================================================================================ presence
def test_presence_away_back_look_and_screen_time():
    tr = G.PresenceTracker(away_after_s=30, back_after_s=1, look_away_s=4)
    face_on = {"nose": (0.5, 0.5), "left_eye": (0.42, 0.45), "right_eye": (0.58, 0.45)}
    face_side = {"nose": (0.62, 0.5), "left_eye": (0.42, 0.45), "right_eye": (0.58, 0.45)}
    assert G.facing(face_on) is True and G.facing(face_side) is False and G.facing(None) is None
    evs = []
    t = 0.0
    for _ in range(100):                     # 10 s at the screen
        evs += tr.feed(t, True, G.facing(face_on)); t += 0.1
    assert evs == ["here"] and 9.5 <= tr.screen_s <= 10.1
    for _ in range(60):                      # 6 s looking away
        evs += tr.feed(t, True, False); t += 0.1
    assert evs[-1] == "look_away"
    screen = tr.screen_s
    evs += tr.feed(t, True, True); t += 0.1
    assert evs[-1] == "look_back" and tr.screen_s == pytest.approx(screen, abs=0.2)
    for _ in range(320):                     # 32 s gone
        evs += tr.feed(t, False); t += 0.1
    assert evs[-1] == "away" and evs.count("away") == 1
    for _ in range(15):
        evs += tr.feed(t, True, True); t += 0.1
    assert evs[-1] == "back"


# ============================================================================================ the service
class FakeBus:
    def __init__(self):
        self.events = []

    async def publish(self, ev, record=True):
        self.events.append(ev)

    async def log(self, *a, **k):
        pass


class FakeQueue:
    def __init__(self, items):
        self.items = {i["id"]: i for i in items}

    def get(self, i):
        return self.items.get(i)

    def pending(self):
        return [i for i in self.items.values() if i.get("status", "pending") == "pending"]


class FakeDaemon:
    def __init__(self, items=()):
        self.bus = FakeBus()
        self.approvals = FakeQueue(list(items))
        self.decided = []

    async def decide_approval(self, item_id, decision, text=""):
        self.decided.append((item_id, decision))


class FakeActions(P.DesktopActions):
    def __init__(self):
        super().__init__(platform="linux", run=self._rec, spawn=self._rec, which=lambda n: "/usr/bin/" + n)
        self.calls = []

    def _rec(self, args):
        self.calls.append(args)
        return True

    def sway(self):
        return True

    def free_workspace(self):
        return 3


def svc(tmp_path, items=(), **kw):
    return P.Presence(FakeDaemon(items), data_dir=tmp_path, actions=FakeActions(), **kw)


def test_off_by_default_and_after_a_restart(tmp_path):
    assert P.DEFAULTS["start_with_orion"] is False
    p = svc(tmp_path)
    assert p.on is False and p.status()["on"] is False and P.camera_on() is False
    p.configure({"lock_when_away": False})
    p2 = svc(tmp_path)
    assert p2.on is False and p2.cfg["lock_when_away"] is False
    saved = json.loads((tmp_path / "presence.json").read_text())
    assert "on" not in saved and saved["start_with_orion"] is False


def test_install_keeps_it_off(tmp_path, monkeypatch):
    monkeypatch.setattr(P, "_instance", svc(tmp_path))
    started = []
    monkeypatch.setattr(P.Presence, "_start_thread", lambda self: started.append(1))
    P.install(FakeDaemon())
    assert started == [] and P._instance.on is False
    P._instance = None


class SecretFrame:
    """Stands in for a camera picture; weak-referenceable so the test can see nobody keeps it."""
    MARK = b"FRAME-PIXELS-DO-NOT-STORE"


def test_frames_are_never_kept_or_written(tmp_path):
    detector_calls = []

    def detector(frame):
        detector_calls.append(type(frame).__name__)
        return [hand("open")], {"nose": (0.5, 0.5), "left_eye": (0.42, 0.45), "right_eye": (0.58, 0.45)}

    p = svc(tmp_path)
    p.detector = detector
    refs = []
    for i in range(40):
        f = SecretFrame()
        refs.append(weakref.ref(f))
        p.step(i / 10, f)
        del f
    p._save_time()
    p._save_cfg()
    gc.collect()
    assert detector_calls and all(r() is None for r in refs), "a frame was kept after its step"
    files = sorted(x.name for x in tmp_path.rglob("*") if x.is_file())
    assert files == ["presence.json", "presence_time.json"]
    for x in tmp_path.rglob("*"):
        if x.is_file():
            assert SecretFrame.MARK not in x.read_bytes()
    assert "SecretFrame" not in json.dumps(p.status()) + json.dumps(p.calibration())


def test_service_loop_with_a_fake_camera_closes_it(tmp_path):
    closed = []

    class Cam:
        def __init__(self, idx):
            pass

        def read(self):
            return SecretFrame()

        def close(self):
            closed.append(1)

    p = svc(tmp_path, camera_factory=Cam, detector_factory=lambda: (lambda f: ([], None)))
    p.cfg["off_on_battery"] = False
    p.set_on(True)
    assert p.on and P.camera_on() is False      # not the installed instance
    p.set_on(False)
    p._thread.join(timeout=3)
    assert not p._thread.is_alive() and closed == [1]


def test_core_signal_carries_the_camera(tmp_path, monkeypatch):
    import core_signals as cs
    p = svc(tmp_path)
    monkeypatch.setattr(P, "_instance", p)
    core = cs.CoreSignals(FakeDaemon())
    assert core.compute(1000)["camera"] is False
    p.on = True
    assert core.compute(1001)["camera"] is True
    p.reason = "A full-screen app is in front, so the camera is resting."
    assert core.compute(1002)["camera"] is False
    P._instance = None


# ============================================================================================ thumbs up: small only
SMALL = {"id": "s1", "kind": "backup_drill", "title": "Restore test passed", "risk": "low", "status": "pending"}


@pytest.mark.parametrize("card", [
    {"id": "b", "kind": "boardroom", "title": "Hire a moderator", "risk": "low"},
    {"id": "m1", "kind": "backup_drill", "title": "Restore test", "amount": 40},
    {"id": "m2", "kind": "security_report", "title": "Pay $12 for a domain"},
    {"id": "m3", "kind": "perf_regression", "summary": "Spend more on the cloud plan"},
    {"id": "m4", "kind": "security_report", "title": "Stardust payout ready"},
    {"id": "h", "kind": "security_report", "title": "Port open", "risk": "high"},
    {"id": "d", "kind": "discord_prompt", "title": "Reply to a fan", "draft": "Thanks!"},
    {"id": "e", "kind": "self_evolve", "title": "Patch"},
    {"id": "x", "kind": "something_new", "title": "Unknown card"},
    None,
])
def test_thumbs_up_refuses_big_money_and_unknown_cards(tmp_path, card):
    assert P.small_card_refusal(card) is not None
    items = [dict(card, status="pending")] if card else []
    p = svc(tmp_path, items)
    res = asyncio.run(p.approve_small_card())
    assert res["ok"] is False and p.d.decided == []


def test_thumbs_up_approves_a_small_card(tmp_path):
    assert P.small_card_refusal(SMALL) is None
    p = svc(tmp_path, [SMALL])
    res = asyncio.run(p.dispatch([{"gesture": "thumbs_up"}]))
    assert res == [{"thumbs_up": {"ok": True, "id": "s1"}}] and p.d.decided == [("s1", "approve")]


def test_thumbs_up_uses_the_card_in_front_and_never_skips_to_another(tmp_path):
    big = {"id": "b1", "kind": "boardroom", "title": "Big", "status": "pending"}
    p = svc(tmp_path, [SMALL, big])
    p.note_card("b1")
    res = asyncio.run(p.approve_small_card())
    assert res["ok"] is False and p.d.decided == []


# ============================================================================================ palm = kill switch
def test_open_palm_triggers_the_freeze(tmp_path, monkeypatch):
    import freeze
    calls = []

    async def fake_freeze(daemon, by, why="", run=None):
        calls.append((by, why))
        return {"frozen": True}

    monkeypatch.setattr(freeze, "freeze", fake_freeze)
    p = svc(tmp_path)
    evs = []
    for i, hands in enumerate(hold("open", 2)):
        evs += p.handle(i / 15, hands, None)
    assert [e for e in evs if e.get("gesture")] == [{"gesture": "stop"}]
    res = asyncio.run(p.dispatch(evs))
    assert {"stop": {"frozen": True}} in res and calls and "palm" in calls[0][0]
    assert any(e.get("event") == "PRESENCE_GESTURE" and e["gesture"] == "stop" for e in p.d.bus.events)


def test_real_freeze_path_freezes(tmp_path):
    import freeze
    freeze.set_dir(tmp_path / "fz")
    try:
        p = svc(tmp_path)
        res = asyncio.run(p.kill_switch())
        assert res == {"frozen": True} and freeze.is_frozen()
    finally:
        freeze.set_dir(None)


# ============================================================================================ actions
def test_gestures_drive_desktops_windows_and_lock(tmp_path):
    p = svc(tmp_path)
    asyncio.run(p.dispatch([{"gesture": "swipe_left"}, {"gesture": "swipe_right"}, {"gesture": "rectangle"},
                            {"gesture": "throw", "direction": "right"}, {"gesture": "throw", "direction": "down"},
                            {"presence": "away"}]))
    assert p.actions.calls == [
        ["swaymsg", "workspace", "next_on_output"], ["swaymsg", "workspace", "prev_on_output"],
        ["swaymsg", "workspace", "number", "3"], ["swaymsg", "move", "container", "to", "workspace", "next_on_output"],
        ["swaymsg", "move", "scratchpad"], ["orion-lock"]]


def test_windows_uses_the_desktop_shortcuts_and_lock(monkeypatch):
    pressed = []
    a = P.DesktopActions(platform="win32", keys=lambda c: pressed.append(c) or True)
    assert a.desktop("next") == "ok" and a.desktop("prev") == "ok" and a.desktop("new") == "ok"
    assert pressed == [("win", "ctrl", "right"), ("win", "ctrl", "left"), ("win", "ctrl", "d")]
    assert "Win+Tab" in a.throw("left")


def test_greeting_has_no_name():
    assert P.greeting(9) == "Morning, welcome back." and P.greeting(20) == "Evening, welcome back."


def test_screen_time_feeds_the_focus_arc(tmp_path):
    p = svc(tmp_path)
    marks = []
    p.d.pacing = type("Pace", (), {"mark_activity": lambda self, ts=None: marks.append(1)})()
    face = {"nose": (0.5, 0.5), "left_eye": (0.42, 0.45), "right_eye": (0.58, 0.45)}
    evs = []
    for i in range(400):
        evs += p.handle(i / 10, [], face)
    asyncio.run(p.dispatch(evs))
    assert 2 <= len(marks) <= 4 and p.status()["screen_time_today_min"] > 0.5


def test_calibration_sets_the_zone(tmp_path):
    p = svc(tmp_path)
    p.calibrate("start")
    for i in range(30):
        p.handle(i / 10, [hand("open", cx=0.3 + i * 0.01, cy=0.4 + i * 0.005)], None)
    res = p.calibrate("finish")
    assert res["ok"] and res["zone"][0] == pytest.approx(0.22, abs=0.02)
    assert json.loads((tmp_path / "presence.json").read_text())["zone"] == res["zone"]
    assert p.calibrate("reset")["zone"] == list(G.DEFAULT_ZONE)


def test_routes(tmp_path, monkeypatch):
    p = svc(tmp_path)
    monkeypatch.setattr(P, "_instance", p)
    monkeypatch.setattr(P, "missing", lambda: ["mediapipe"])
    p.camera_factory = P.OpenCVCamera            # the real camera path: refuses without the add-on
    app = FastAPI()
    app.include_router(P.make_router(lambda: p.d))
    c = TestClient(app)
    st = c.get("/presence").json()
    assert st["on"] is False and st["missing"] == ["mediapipe"]
    st = c.post("/presence", json={"action": "toggle"}).json()
    assert st["on"] is False and "pip install mediapipe" in st["error"]
    assert c.post("/presence/config", json={"pause_media": True, "fps": 99}).json()["config"]["fps"] == 15
    assert c.get("/presence/calibration").json()["hand"] is None
    assert c.post("/presence/current-card", json={"id": "s1"}).json() == {"ok": True}
    P._instance = None
