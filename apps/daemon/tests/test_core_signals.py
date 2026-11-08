"""Art stage 6: the daemon side of the black hole's smart reactions (signal shaping, batching, taps, routes)."""
import asyncio

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import core_signals as cs


@pytest.fixture(autouse=True)
def _clean_externals():
    cs._external.clear()
    yield
    cs._external.clear()


# ------------------------------------------------------------------ shaping
def test_thinking_is_gentle_and_capped():
    assert cs.thinking_level(0, 0) == 0
    one = cs.thinking_level(1, 0)
    assert 0.4 < one < 0.7
    assert one < cs.thinking_level(2, 0) < cs.thinking_level(6, 0) <= 1
    assert 0 < cs.thinking_level(0, 1) < one  # a working coding agent counts, but less than a model call
    assert cs.thinking_level(-3, -1) == 0


def test_heat_is_capped():
    assert cs.heat_level(0) == 0
    assert 0 < cs.heat_level(0.05) < cs.heat_level(0.5) < cs.HEAT_CAP
    assert cs.heat_level(1000) == cs.HEAT_CAP


def test_turbulence_ignores_normal_load():
    assert cs.turbulence_level(None) == 0
    assert cs.turbulence_level(0.3) == 0
    assert 0 < cs.turbulence_level(0.7) < cs.turbulence_level(1.0) == 0.7


def test_battery_dims_only_when_low_and_unplugged():
    assert cs.battery_dim(None, None) == 0
    assert cs.battery_dim(10, True) == 0
    assert cs.battery_dim(60, False) == 0
    assert cs.battery_dim(25, False) == 0
    assert 0 < cs.battery_dim(15, False) < cs.battery_dim(5, False) == 0.6
    assert cs.battery_dim(1, None) == 0.6


def test_spend_window_counts_only_the_last_hour():
    w = cs.SpendWindow()
    w.add(0, 1.00)
    w.add(1800, 1.20)
    assert w.last_hour() == pytest.approx(0.20)
    w.add(4000, 1.50)       # baseline moves to the 1800 s sample
    assert w.last_hour() == pytest.approx(0.30)
    w.add(4100, 0.10)       # the meter restarted
    assert w.last_hour() == 0


def test_comet_size():
    assert cs.comet_size() == "normal"
    assert cs.comet_size(vip=True) == "ember"
    assert cs.comet_size(level="urgent") == "ember"
    assert cs.comet_size(muted=True) == "tiny"
    assert cs.comet_size(level="digest") == "tiny"


def test_comets_are_spaced_and_a_flood_folds_into_one():
    q = cs.CometQueue()
    for _ in range(50):
        q.push("normal", 0)
    first = q.drain(0)
    assert first == [{"kind": "comet", "size": "normal", "count": cs.COUNT_MAX}]
    assert q.drain(1) == []                 # nothing left and the gap holds anyway
    q.push("normal", 1)
    assert q.drain(1) == []                 # too soon after the last one
    assert q.drain(cs.COMET_GAP_S + 0.1)[0]["count"] == 1


def test_burst_limit_per_minute():
    q = cs.CometQueue()
    sent = 0
    t = 0.0
    while t < 59:
        q.push("normal", t)
        sent += len(q.drain(t))
        t += cs.COMET_GAP_S
    assert sent == cs.COMET_BURST


def test_ember_goes_first_and_digest_waits_to_fall_together():
    q = cs.CometQueue()
    q.push("tiny", 0)
    q.push("tiny", 1)
    q.push("ember", 2)
    assert q.drain(2)[0]["size"] == "ember"
    assert q.drain(10) == []                # digest is held
    out = q.drain(cs.DIGEST_HOLD_S + 1)
    assert out == [{"kind": "comet", "size": "tiny", "count": 2}]


def test_resolution_and_orbit():
    assert cs.resolution({"status": "sent"}) == "approve"
    assert cs.resolution({"status": "sending"}) == "approve"
    assert cs.resolution({"status": "rejected"}) == "reject"
    assert cs.resolution({"status": "pending"}) is None
    pending = [{"id": f"a{i}", "kind": "boardroom" if i == 0 else "discord_prompt"} for i in range(10)]
    stars, total = cs.orbit_of(pending)
    assert total == 10 and len(stars) == cs.ORBIT_MAX
    assert stars[0] == {"id": "a0", "tone": "boardroom"} and stars[1]["tone"] == "approval"


def test_fullscreen_from_sway_tree():
    game = {"nodes": [{"nodes": [{"focused": True, "fullscreen_mode": 1, "app_id": "steam_app_123"}]}]}
    orion = {"nodes": [{"focused": True, "fullscreen_mode": 1, "app_id": "orion-hud"}]}
    windowed = {"nodes": [{"focused": True, "fullscreen_mode": 0, "app_id": "foot"}]}
    assert cs.fullscreen_from_tree(game) is True
    assert cs.fullscreen_from_tree(orion) is False
    assert cs.fullscreen_from_tree(windowed) is False
    assert cs.fullscreen_from_tree({}) is False


def test_cpu_reader_uses_proc_stat_deltas(tmp_path):
    p = tmp_path / "stat"
    p.write_text("cpu  100 0 100 800 0 0 0 0 0 0\n")
    r = cs.CpuReader(str(p))
    assert r.read() is None                 # first sample has nothing to compare
    p.write_text("cpu  250 0 250 900 0 0 0 0 0 0\n")  # 300 busy of 400
    assert r.read() == pytest.approx(0.75)


def test_battery_reader_from_sys(tmp_path):
    bat = tmp_path / "BAT0"
    bat.mkdir()
    (bat / "type").write_text("Battery\n")
    (bat / "capacity").write_text("12\n")
    (bat / "status").write_text("Discharging\n")
    ac = tmp_path / "AC"
    ac.mkdir()
    (ac / "type").write_text("Mains\n")
    assert cs.read_battery(str(tmp_path)) == (12.0, False)
    (bat / "status").write_text("Charging\n")
    assert cs.read_battery(str(tmp_path)) == (12.0, True)
    (bat / "type").write_text("Mains\n")
    assert cs.read_battery(str(tmp_path)) == (None, None)  # a desktop: no battery, no dimming


def test_external_signals():
    assert cs.set_external_signal("nope", 1) is False
    assert cs.set_external_signal("update_ready", True, now=0)
    assert cs.external("update_ready", now=10_000) == 1.0      # never expires
    cs.set_external_signal("cpu", 3, now=0)
    assert cs.external("cpu", now=1) == 1.0                    # clamped
    assert cs.external("cpu", now=cs.EXTERNAL_TTL_S + 5) is None
    cs.set_external_signal("battery", 140, now=0)
    assert cs.external("battery", now=1) == 100.0


# ------------------------------------------------------------------ the live service with a fake daemon
class _Bus:
    def __init__(self):
        self.events = []

    async def publish(self, event, *, audience="ui", record=True):
        self.events.append(event)


class _Meter:
    def __init__(self):
        self.cost = 0.0

    def brief(self):
        return {"session_cost": self.cost}


class _Orch:
    def __init__(self):
        self.meter = _Meter()
        self.gate = None

    async def handle(self, inp):
        await self.gate.wait()
        return "ok"


class _Approvals:
    def __init__(self):
        self.items = []

    def pending(self):
        return [i for i in self.items if i["status"] == "pending"]


class _Alerts:
    def __init__(self):
        self.level = "normal"

    def quiet_hours(self):
        return list(range(24))

    def effective_level(self, kind):
        return self.level

    def post(self, kind, title, body="", ref=None, card=None):
        return {"type": kind, "level": "urgent" if kind == "system" else "normal", "title": title}


class _Toolkit:
    def __init__(self):
        self.list = []

    def agents(self):
        return self.list


class _Daemon:
    def __init__(self):
        self.bus = _Bus()
        self.orchestrator = _Orch()
        self.approvals = _Approvals()
        self.alerts = _Alerts()
        self.agent_toolkit = _Toolkit()


def _service():
    d = _Daemon()
    clock = [1000.0]
    core = cs.CoreSignals(d, clock=lambda: clock[0])
    core.attach()
    return d, core, clock


def test_snapshot_reflects_daemon_state():
    d, core, _ = _service()
    d.approvals.items = [{"id": f"x{i}", "kind": "discord_prompt", "status": "pending"} for i in range(9)]
    d.agent_toolkit.list = [{"state": "working"}, {"state": "done"}]
    cs.set_external_signal("update_ready", 1)
    snap = core.compute(1000, cpu=0.95, battery=(8, False), fullscreen=True)
    assert snap["orbit_total"] == 9 and len(snap["orbit"]) == 7
    assert 0 < snap["thinking"] < cs.thinking_level(1, 0)
    assert snap["turbulence"] > 0.5 and snap["dim"] > 0.4
    assert snap["update_ready"] == 1 and snap["paused"] is True and snap["quiet"] == 1
    d.orchestrator.meter.cost = 0.0
    core.compute(1001)
    d.orchestrator.meter.cost = 2.0
    assert core.compute(1002)["heat"] == cs.HEAT_CAP


def test_orchestrator_calls_are_counted_and_taps_undo():
    async def run():
        d, core, _ = _service()
        d.orchestrator.gate = asyncio.Event()
        task = asyncio.create_task(d.orchestrator.handle("hi"))
        await asyncio.sleep(0)
        assert core.inflight == 1
        assert core.compute(1000)["thinking"] > 0.4
        d.orchestrator.gate.set()
        assert await task == "ok"
        assert core.inflight == 0
        core.detach()
        assert d.orchestrator.handle.__name__ == "handle" and not hasattr(d.orchestrator.handle, "__wrapped__")
    asyncio.run(run())


def test_bus_tap_turns_messages_and_decisions_into_core_events(monkeypatch):
    monkeypatch.setattr(cs, "read_fullscreen", lambda: None)
    monkeypatch.setattr(cs, "read_battery", lambda: (None, None))
    monkeypatch.setattr(cs.CpuReader, "read", lambda self: None)  # this machine's real load must not leak in

    async def run():
        d, core, clock = _service()
        d.approvals.items = [{"id": "a1", "kind": "discord_prompt", "status": "pending"}]
        await d.bus.publish({"event": "COMMS_MESSAGE", "message": {"direction": "in", "vip": True}, "thread": {}})
        await d.bus.publish({"event": "COMMS_MESSAGE", "message": {"direction": "out"}, "thread": {}})
        await d.bus.publish({"event": "COMMS_MESSAGE", "message": {"direction": "in", "backfill": True}, "thread": {}})
        out = await core.step()
        kinds = [(e["event"], e.get("kind"), e.get("size")) for e in out]
        assert ("CORE_SIGNALS", None, None) in kinds
        assert ("CORE_EVENT", "comet", "ember") in kinds
        assert sum(1 for k in kinds if k[1] == "comet") == 1
        sig = next(e for e in out if e["event"] == "CORE_SIGNALS")
        assert sig["orbit"] == [{"id": "a1", "tone": "approval"}]

        d.approvals.items[0]["status"] = "rejected"
        await d.bus.publish({"event": "APPROVAL_RESOLVED", "item": {"id": "a1", "status": "rejected"}})
        await d.bus.publish({"event": "APPROVAL_RESOLVED", "item": {"id": "a1", "status": "rejected"}})  # once only
        clock[0] += 1
        out = await core.step()
        orbit_events = [e for e in out if e.get("kind") == "orbit"]
        assert orbit_events == [{"event": "CORE_EVENT", "kind": "orbit", "id": "a1", "outcome": "reject"}]
        sig = next(e for e in out if e["event"] == "CORE_SIGNALS")
        assert sig["orbit"] == [] and sig["orbit_total"] == 0

        # an urgent system alert is an ember comet; the vault swallowing a file is a flare
        d.alerts.post("system", "Disk nearly full")
        d.alerts.post("approval", "Approve?")
        await d.bus.publish({"event": "BRIEFING", "name": "notes.md", "text": "..."})
        clock[0] += cs.COMET_GAP_S + 0.1
        out = await core.step()
        assert {"event": "CORE_EVENT", "kind": "feed"} in out
        assert [e["size"] for e in out if e.get("kind") == "comet"] == ["ember"]

        # nothing changed: no new snapshot until the heartbeat
        clock[0] += 1
        out = await core.step()
        assert not [e for e in out if e["event"] == "CORE_SIGNALS"]
        clock[0] += cs.HEARTBEAT_S
        out = await core.step()
        assert [e for e in out if e["event"] == "CORE_SIGNALS"]
        core.detach()
    asyncio.run(run())


def test_routes():
    app = FastAPI()
    app.include_router(cs.make_router(lambda: None))
    c = TestClient(app)
    assert c.post("/core/signal", json={"name": "update_ready", "value": 1}).json() == {"ok": True}
    assert cs.external("update_ready") == 1.0
    assert c.post("/core/signal", json={"name": "rm -rf", "value": 1}).json()["ok"] is False
    assert c.post("/core/signal", json={"name": "cpu", "value": 500}).status_code == 422
    assert c.get("/core/signals").status_code == 200
