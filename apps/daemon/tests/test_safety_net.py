"""Safety net (complete plan step 3): change history, kill switch, snapshots (daemon side) and the health monitor.
Nothing here touches the real system: fake sysfs/proc trees, a fake orion-snapshot, a fake helper."""
import asyncio
import json
import os
import sqlite3
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

import boardroom as br
import changes
import freeze
import health_monitor as hm
import snapshots


def run(coro):
    return asyncio.run(coro)


class FakeBus:
    def __init__(self):
        self.events, self.logs = [], []
        self.clients = {}

    async def publish(self, e, **k):
        self.events.append(e)

    async def log(self, m, level="info"):
        self.logs.append((level, m))


class FakeDaemon:
    def __init__(self, tmp):
        self.board = br.Boardroom(Path(tmp) / "board")
        self.bus = FakeBus()
        self._tasks = []


@pytest.fixture()
def safe(tmp_path, monkeypatch):
    """Fresh change log, freeze state and history files for each test; alerts captured."""
    changes.install(changes.ChangeLog(tmp_path / "changes.db"))
    freeze.set_dir(tmp_path / "data")
    monkeypatch.setenv("ORION_APPS_HISTORY", str(tmp_path / "apps.jsonl"))
    monkeypatch.setenv("ORION_SYSTEM_CHANGES", str(tmp_path / "system.jsonl"))
    monkeypatch.setattr(freeze, "HELPER", str(tmp_path / "no-helper"))
    import alerts
    sent = []
    monkeypatch.setattr(alerts, "emit", lambda *a, **k: sent.append(a))
    yield {"tmp": tmp_path, "alerts": sent}
    freeze.set_dir(None)
    changes.install(None)


# =====================================================================================  change history
def test_change_log_is_append_only(safe):
    log = changes.current()
    c = log.add("Updated Firefox", why="weekly updates", kind="app_update", approved_by="you",
                tests=[{"name": "starts", "after": True}])
    assert log.get(c["id"])["what"] == "Updated Firefox"
    with pytest.raises(sqlite3.DatabaseError):
        log.db.execute("DELETE FROM changes")
    with pytest.raises(sqlite3.DatabaseError):
        log.db.execute("UPDATE changes SET what='something else'")
    log.mark_undone(c["id"], "chg-x")
    with pytest.raises(sqlite3.DatabaseError):            # undone_by is set once, never changed again
        log.db.execute("UPDATE changes SET undone_by='chg-y'")
    with pytest.raises(ValueError):
        log.add("   ")


def test_record_change_never_raises(safe, monkeypatch):
    monkeypatch.setattr(changes.ChangeLog, "add", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("disk full")))
    assert changes.record_change("x") is None


def test_list_merges_get_apps_and_system_history(safe):
    t = safe["tmp"]
    (t / "apps.jsonl").write_text(json.dumps({"id": "chg-app1", "ts": time.time() - 50, "kind": "app_update",
                                              "app": "org.mozilla.firefox", "name": "Firefox", "why": "weekly",
                                              "undo": ["flatpak", "update", "--system", "--commit=abc",
                                                       "org.mozilla.firefox"]}) + "\nnot json\n")
    (t / "system.jsonl").write_text(json.dumps({"id": "sys-1", "ts": time.time() - 10, "kind": "snapshot",
                                                "what": "Took a snapshot (before an update)",
                                                "undo": {"kind": "snapshot_restore", "snapshot": "7",
                                                         "sensitive": True}}) + "\n")
    changes.record_change("Froze Orion", kind="freeze")
    items = changes.current().list()
    assert [i["what"] for i in items] == ["Froze Orion", "Took a snapshot (before an update)", "Updated Firefox"]
    app = items[2]
    assert app["source"] == "get-apps" and app["undo"]["kind"] == "argv" and app["undo"]["sensitive"] is False
    assert changes.current().get("sys-1")["undo"]["snapshot"] == "7"


def test_app_history_undo_only_runs_known_programs(safe):
    t = safe["tmp"]
    (t / "apps.jsonl").write_text(json.dumps({"id": "chg-bad", "ts": 1, "kind": "app_install", "name": "X",
                                              "undo": ["rm", "-rf", "/"]}) + "\n" +
                                  json.dumps({"id": "chg-rm", "ts": 2, "kind": "app_install", "name": "Y",
                                              "undo": ["orion-apps", "remove", "org.y.Y"]}) + "\n")
    assert changes.current().get("chg-bad")["undo"] is None
    assert changes.current().get("chg-rm")["undo"]["sensitive"] is True     # removing an app asks first


def test_one_tap_undo_and_sensitive_undo(safe):
    d = FakeDaemon(safe["tmp"])
    ran = []
    changes.register_undo("test_put_back", lambda dmn, c, u: ran.append(u["value"]) or {"ok": True, "message": "Back."})
    easy = changes.record_change("Changed the volume", undo={"kind": "test_put_back", "value": 40, "label": "Put back"})
    res = run(changes.undo(d, easy["id"]))
    assert res["ok"] and ran == [40]
    assert changes.current().get(easy["id"])["undone_by"]
    assert run(changes.undo(d, easy["id"]))["message"] == "This change was already undone."

    big = changes.record_change("Installed an update", undo={"kind": "test_put_back", "value": 1, "sensitive": True})
    changes.install_executor(d)
    res = run(changes.undo(d, big["id"]))
    assert res["pending"] and ran == [40]                                   # nothing ran yet
    dec = d.board.get(res["decision"])
    assert dec["task_type"] == changes.UNDO_TASK and br.TASK_TYPES[changes.UNDO_TASK].max_level == 1

    async def approve():
        await d.board.verdict(dec["id"], "approve", dec["packet_hash"])
        for _ in range(50):
            await asyncio.sleep(0.02)
            if len(ran) == 2:
                break
    run(approve())
    assert ran == [40, 1]
    with pytest.raises(KeyError):
        run(changes.undo(d, "chg-missing"))


def test_changes_routes(safe):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    d = FakeDaemon(safe["tmp"])
    app = FastAPI()
    app.include_router(changes.make_router(lambda: d))
    c = TestClient(app)
    r = c.post("/changes", json={"what": "orion-apps changed something", "undo": {"kind": "evil", "cmd": "x"}})
    assert r.status_code == 200 and r.json()["change"]["undo"] is None     # outside tools can't invent undo kinds
    assert c.post("/changes", json={"what": ""}).status_code == 400
    lst = c.get("/changes").json()["changes"]
    assert lst[0]["what"] == "orion-apps changed something"
    assert c.post(f"/changes/{lst[0]['id']}/undo").json()["ok"] is False
    assert c.get("/changes/none").status_code == 404


# =====================================================================================  kill switch
def test_freeze_blocks_and_unfreeze_needs_approval(safe):
    d = FakeDaemon(safe["tmp"])
    freeze.install(d)
    assert not freeze.is_frozen() and freeze.blocked("x") is None
    st = run(freeze.freeze(d, "you", "testing"))
    assert st["frozen"] and freeze.is_frozen()
    assert (safe["tmp"] / "data" / "frozen").exists()                      # marker for shell tools
    assert "won't replay skills" in freeze.blocked("replay skills")
    assert any(a[1] == "Orion is frozen" for a in safe["alerts"])
    assert d.bus.events[-1]["event"] == "FREEZE_STATE"
    row = changes.current().list()[0]
    assert row["kind"] == "freeze" and row["undo"]["kind"] == "unfreeze" and row["undo"]["sensitive"]

    res = run(freeze.request_unfreeze(d, "voice"))
    assert res["pending"] and freeze.is_frozen()                           # asking doesn't unfreeze
    again = run(freeze.request_unfreeze(d, "voice"))
    assert again["decision"] == res["decision"]                             # one request at a time
    dec = d.board.get(res["decision"])

    async def approve():
        await d.board.verdict(dec["id"], "approve", dec["packet_hash"])
        await asyncio.sleep(0.05)
    run(approve())
    assert not freeze.is_frozen() and not (safe["tmp"] / "data" / "frozen").exists()


def test_freeze_text_phrases(safe):
    d = FakeDaemon(safe["tmp"])
    assert run(freeze.handle_text(d, "what's the weather")) is None
    out = run(freeze.handle_text(d, "Orion, freeze!", "voice"))
    assert out.startswith("Frozen.") and freeze.is_frozen()
    out = run(freeze.handle_text(d, "ok orion unfreeze please"))
    assert "phone" in out and freeze.is_frozen()


def test_freeze_pauses_only_agents_orion_started(safe, monkeypatch):
    d = FakeDaemon(safe["tmp"])
    signals = []
    runs = {"r1": SimpleNamespace(proc=SimpleNamespace(pid=111)), "r2": SimpleNamespace(proc=SimpleNamespace(pid=222))}
    swarm = SimpleNamespace(
        runner=SimpleNamespace(runs=runs), paused=set(),
        launched=lambda: [{"id": "r1", "running": True, "paused": False},
                          {"id": "r2", "running": False, "paused": False}],
        _signal=lambda pid, action: signals.append((pid, action)))
    d.agent_toolkit = SimpleNamespace(swarm=swarm)
    st = run(freeze.freeze(d, "phone"))
    assert st["paused_agents"] == 1 and signals == [(111, "pause")] and swarm.paused == {"r1"}
    run(freeze.unfreeze(d, "phone"))
    assert signals[-1] == (111, "resume") and swarm.paused == set()


def test_freeze_pauses_system_updates_through_helper(safe, monkeypatch):
    helper = safe["tmp"] / "helper"
    helper.write_text("#!/bin/sh\nexit 0\n")
    helper.chmod(0o755)
    monkeypatch.setattr(freeze, "HELPER", str(helper))
    monkeypatch.setattr(freeze.shutil, "which", lambda n: "/usr/bin/pkexec")
    calls = []

    def fake_run(argv, **k):
        calls.append(argv)
        return SimpleNamespace(returncode=0, stderr="", stdout="")
    assert freeze._system_pause(True, fake_run) == "Automatic system updates paused."
    assert calls[-1] == ["pkexec", str(helper), "on"]
    assert "back on" in freeze._system_pause(False, fake_run)
    assert "refused" in freeze._system_pause(True, lambda a, **k: SimpleNamespace(returncode=126, stderr="no"))


def test_guards_in_other_modules(safe, tmp_path):
    d = FakeDaemon(safe["tmp"])
    run(freeze.freeze(d, "you"))
    # decisions that would run on their own wait while frozen
    board = d.board
    board.learner.set_level("merge.docs", 3)
    dec = run(board.propose("merge.docs", "docs", {}, "test"))
    assert dec["status"] == "pending"
    # MCP tools that change things
    import mcp_routes

    async def job():
        return {"state": "done"}
    with pytest.raises(ValueError, match="frozen"):
        mcp_routes._spawn({"id": "dec-x"}, job())
    # self-evolve
    from self_evolve import PatchError, SelfEvolveEngine
    eng = SelfEvolveEngine(None, tmp_path, tmp_path)
    with pytest.raises(PatchError, match="frozen"):
        run(eng.propose("add a button"))
    with pytest.raises(PatchError, match="frozen"):
        eng.apply("p1")
    run(freeze.unfreeze(d, "you"))
    board.learner.set_level("merge.docs", 3)
    assert run(board.propose("merge.docs", "docs", {}, "test"))["status"] == "auto_executed"


def test_phone_routes(safe):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    d = FakeDaemon(safe["tmp"])
    app = FastAPI()
    seen = []

    async def phone_auth(request, need):
        seen.append(need)
        return {"name": "Pixel", "fingerprint": "AB:CD"}, None

    async def json_body(request):
        return await request.json() if await request.body() else {}
    freeze.phone_hook(d)(app, phone_auth, json_body)
    c = TestClient(app)
    assert c.post("/phone/freeze", json={"why": "away"}).json()["freeze"]["frozen"] is True
    assert c.get("/phone/freeze").json()["freeze"]["frozen"] is True
    assert c.post("/phone/unfreeze", json={}).json()["freeze"]["frozen"] is False
    assert seen == ["sign", "read", "sign"]                                 # freeze and unfreeze need the fingerprint


# =====================================================================================  snapshots (daemon side)
@pytest.fixture()
def fake_cli(tmp_path, monkeypatch):
    log = tmp_path / "cli-calls.txt"
    cli = tmp_path / "orion-snapshot"
    cli.write_text(
        f"#!{sys.executable}\n"
        "import json, sys\n"
        f"open({str(log)!r}, 'a').write(' '.join(sys.argv[1:]) + '\\n')\n"
        "if sys.argv[1] == 'take':\n"
        "    print(json.dumps({'ok': True, 'snapshot': {'id': '12'}, 'message': 'Snapshot 12 taken.'}))\n"
        "else:\n"
        "    print(json.dumps({'backend': 'btrfs', 'snapshots': [{'id': '11', 'kind': 'pre-update', 'kind_words':"
        " 'Before an update', 'date': '2026-10-10 03:30', 'why': 'apt'}], 'undo_to': '11', 'words': 'x'}))\n")
    cli.chmod(0o755)
    monkeypatch.setenv("ORION_SNAPSHOT_CLI", str(cli))
    monkeypatch.setenv("ORION_SNAP_INDEX", str(tmp_path / "missing-index.json"))
    return log


def test_snapshots_status_take_and_restore_request(safe, fake_cli):
    d = FakeDaemon(safe["tmp"])
    opened = []
    s = snapshots.Snapshots(as_root=True)
    snapshots.install(d, s, open_terminal=lambda cmd: opened.append(cmd) or True)
    assert s.status()["undo_to"] == "11"
    assert s.take("before trying a driver")["ok"]
    assert "take --kind manual --why before trying a driver --json" in fake_cli.read_text()
    with pytest.raises(LookupError):
        run(snapshots.request_restore(d, s, "99"))
    with pytest.raises(ValueError):
        run(snapshots.request_restore(d, s, "11", path="../etc/shadow"))
    with pytest.raises(ValueError):
        run(snapshots.request_restore(d, s, "11; rm -rf /"))
    res = run(snapshots.request_restore(d, s, "11", path="/home/orion/notes.txt"))
    assert res["pending"] and opened == []                                   # nothing happens before approval
    dec = d.board.get(res["decision"])
    assert br.TASK_TYPES[snapshots.RESTORE_TASK].sensitive and br.TASK_TYPES[snapshots.RESTORE_TASK].max_level == 1
    run(d.board.verdict(dec["id"], "approve", dec["packet_hash"]))
    assert opened == ["su -P - orion-admin -c 'sudo orion-snapshot restore 11 --yes --path /home/orion/notes.txt'"]
    assert changes.current().list()[0]["kind"] == "restore"


def test_snapshots_index_file_wins(safe, tmp_path, monkeypatch):
    idx = tmp_path / "index.json"
    idx.write_text(json.dumps({"backend": "vault", "snapshots": [], "words": "0 copies"}))
    monkeypatch.setenv("ORION_SNAP_INDEX", str(idx))
    monkeypatch.setenv("ORION_SNAPSHOT_CLI", str(tmp_path / "not-there"))
    assert snapshots.Snapshots().status()["backend"] == "vault"
    idx.unlink()
    assert snapshots.Snapshots().status()["backend"] == "none"
    assert snapshots.Snapshots(as_root=False)._root_argv("take")[0] == "pkexec"


def test_snapshot_routes(safe, fake_cli):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    d = FakeDaemon(safe["tmp"])
    app = FastAPI()
    app.include_router(snapshots.make_router(lambda: d, snapshots.Snapshots(as_root=True)))
    c = TestClient(app)
    assert c.get("/snapshots").json()["backend"] == "btrfs"
    assert c.post("/snapshots", json={"why": "now"}).json()["ok"]
    assert c.post("/snapshots/undo").json()["pending"]
    assert c.post("/snapshots/404/restore", json={}).status_code == 404


# =====================================================================================  health monitor
def make_world(tmp: Path, *, temp=48000, cap=80, status="Discharging", net_up=True, mem_avail_kb=6_000_000,
               sway=True) -> hm.Probes:
    sysr, proc = tmp / "sys", tmp / "proc"
    tz = sysr / "class/thermal/thermal_zone0"
    tz.mkdir(parents=True)
    (tz / "temp").write_text(f"{temp}\n")
    (tz / "type").write_text("x86_pkg_temp\n")
    bat = sysr / "class/power_supply/BAT0"
    bat.mkdir(parents=True)
    for k, v in {"type": "Battery", "capacity": cap, "status": status, "energy_full": 40, "energy_full_design": 50}.items():
        (bat / k).write_text(f"{v}\n")
    ac = sysr / "class/power_supply/AC"
    ac.mkdir(parents=True)
    (ac / "type").write_text("Mains\n")
    for n, st in (("lo", "unknown"), ("wlan0", "up" if net_up else "down")):
        (sysr / "class/net" / n).mkdir(parents=True)
        (sysr / "class/net" / n / "operstate").write_text(st + "\n")
    (proc / "net").mkdir(parents=True)
    (proc / "net/route").write_text("Iface\tDestination\tGateway\nwlan0\t00000000\t0101A8C0\n")
    (proc / "meminfo").write_text(f"MemTotal: 16000000 kB\nMemFree: 1000 kB\nMemAvailable: {mem_avail_kb} kB\n")
    (proc / "123").mkdir()
    (proc / "123/comm").write_text("sway\n" if sway else "bash\n")
    failed = {"system": "", "user": ""}

    def fake_run(argv, timeout=8):
        return 0, failed["user" if "--user" in argv else "system"]
    Usage = SimpleNamespace
    p = hm.Probes(sys_root=sysr, proc_root=proc, run=fake_run,
                  http_json=lambda url, t=2.0: {"models": [{"name": "llama3.1:8b"}]},
                  disk_usage=lambda path: Usage(total=500 * 1024 ** 3, used=0, free=200 * 1024 ** 3),
                  disk_paths=("/",), platform="linux", orionos=True, now=lambda: 1_000_000.0,
                  import_ok=lambda m: True)
    p.failed = failed
    return p


CFG = SimpleNamespace(ollama_url="http://127.0.0.1:11434", ollama_model="llama3.1:8b", gemini_api_key="",
                      anthropic_api_key="", openai_api_key="")


def test_health_all_fine(safe, tmp_path):
    d = FakeDaemon(safe["tmp"])
    d.bus.clients = {"a": SimpleNamespace(kind="desktop")}
    d.board.phones._phones = lambda: {"p1": {"last_ts": int(1_000_000.0 * 1000) - 60_000}}
    m = hm.HealthMonitor(d, make_world(tmp_path), cfg=CFG)
    snap = run(m.check_now())
    bad = [c for c in snap["checks"] if c["status"] != "ok"]
    assert bad == [] and snap["status"] == "ok" and snap["turbulence"] == 0
    assert {c["id"] for c in snap["checks"]} >= {"hud", "daemon", "ai", "voice", "phone", "network", "disk", "memory",
                                                  "temperature", "battery", "services", "desktop"}
    assert d.bus.events[-1]["event"] == "HEALTH_STATUS"
    assert "a moment ago" in next(c for c in snap["checks"] if c["id"] == "phone")["reason"]


def test_health_problems_turbulence_and_one_alert(safe, tmp_path):
    d = FakeDaemon(safe["tmp"])
    p = make_world(tmp_path, temp=97000, cap=5, net_up=False, mem_avail_kb=500_000, sway=False)
    p.failed["system"] = "orion-snapshot-nightly.service loaded failed failed nightly\n"
    p.failed["user"] = "foo.service loaded failed failed Foo\n"
    p.http_json = lambda url, t=2.0: None
    m = hm.HealthMonitor(d, p, cfg=CFG)
    snap = run(m.check_now())
    by = {c["id"]: c for c in snap["checks"]}
    assert by["temperature"]["status"] == "fail" and by["temperature"]["fix"] == "cool_down"
    assert by["battery"]["status"] == "fail" and by["battery"]["fix"] == "plug_in"
    assert by["network"]["status"] == "fail" and by["memory"]["status"] == "fail"
    assert by["ai"]["status"] == "fail" and by["ai"]["fix"] == "setup_ai"
    assert by["desktop"]["status"] == "fail" and by["services"]["status"] == "fail"
    assert "foo.service (yours)" in by["services"]["detail"]
    assert by["phone"]["status"] == "warn" and by["hud"]["status"] == "warn"
    assert snap["status"] == "fail" and snap["turbulence"] == 1.0
    n = len(safe["alerts"])
    assert n == sum(1 for c in snap["checks"] if c["status"] == "fail")
    run(m.check_now())
    assert len(safe["alerts"]) == n                                          # deduped: no repeat a minute later
    p.now = lambda: 1_000_000.0 + hm.REALERT_S + 1
    run(m.check_now())
    assert len(safe["alerts"]) == 2 * n                                      # reminded after 12 hours


def test_health_warnings_and_hud_ping(safe, tmp_path):
    d = FakeDaemon(safe["tmp"])
    p = make_world(tmp_path, cap=12, status="Discharging")
    p.import_ok = lambda m: m != "kokoro"
    m = hm.HealthMonitor(d, p, cfg=CFG)
    m.hud_seen()
    snap = run(m.check_now())
    by = {c["id"]: c for c in snap["checks"]}
    assert by["hud"]["status"] == "ok"
    assert by["battery"]["status"] == "warn" and by["voice"]["status"] == "warn"
    assert 0 < snap["turbulence"] < 0.6 and snap["status"] == "warn"
    run(freeze.freeze(d, "you"))
    assert {c["id"]: c for c in run(m.check_now())["checks"]}["freeze"]["status"] == "warn"


def test_health_battery_wear_and_model_missing(safe, tmp_path):
    p = make_world(tmp_path, cap=90, status="Charging")
    (p.sys_root / "class/power_supply/BAT0/energy_full").write_text("20\n")
    assert hm.check_battery(p)["fix"] == "replace_battery"
    p.http_json = lambda url, t=2.0: {"models": [{"name": "phi3:mini"}]}
    r = hm.check_ai(p, CFG)
    assert r["status"] == "warn" and r["fix"] == "pull_model"
    d = FakeDaemon(safe["tmp"])

    async def boom():
        raise RuntimeError("x")

    async def mk():
        t = asyncio.get_running_loop().create_task(boom(), name="telemetry")
        try:
            await t
        except RuntimeError:
            pass
        d._tasks = [t]
        return hm.check_daemon(d, p)
    r = run(mk())
    assert r["status"] == "fail" and "telemetry" in r["reason"]


def test_health_route_marks_hud(safe, tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    d = FakeDaemon(safe["tmp"])
    m = hm.HealthMonitor(d, make_world(tmp_path), cfg=CFG)
    app = FastAPI()
    app.include_router(hm.make_router(lambda: m))
    c = TestClient(app)
    body = c.get("/health/status?source=hud").json()
    assert m.last_hud == 1_000_000.0 and body["checks"]
    assert c.post("/health/check").json()["status"] in ("ok", "warn", "fail")


def test_live_stick_has_no_false_alarms(tmp_path):
    """On the USB stick: casper's md5 check service and a small in-memory / don't count as problems."""
    p = make_world(tmp_path)
    (p.proc_root / "cmdline").write_text("boot=casper live-media-path=/slots/a persistent quiet\n")
    p.failed["system"] = "casper-md5check.service loaded failed failed casper-md5check Verify Live ISO checksums\n"
    p.disk_usage = lambda path: SimpleNamespace(total=1500 * 1024 ** 2, used=0, free=1400 * 1024 ** 2)
    assert hm.check_services(p)["status"] == "ok"
    assert hm.check_disk(p)["status"] == "ok"
    (p.proc_root / "cmdline").write_text("root=UUID=x ro quiet\n")  # installed system: 1.4 GB free is worth a warning
    assert hm.check_disk(p)["status"] == "warn"
