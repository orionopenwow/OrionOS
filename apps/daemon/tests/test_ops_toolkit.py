"""ops_toolkit wiring: phrases through quick commands, HTTP routes, MCP tools and boardroom task types for
Project Runbook, Release Butler, DB Time Machine and Raid Night Autopilot."""
import asyncio
import json
import types

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import boardroom as br
import ops_toolkit
from quick_commands import QuickCommands
from test_runbook import FakeRunner, node_project


class FakeWow:
    enabled = False

    def __init__(self):
        self.on_crash = []


@pytest.fixture()
def daemon(tmp_path):
    d = types.SimpleNamespace()
    d.board = br.Boardroom(tmp_path / "board")
    d.quick = QuickCommands(config=tmp_path / "quick.json", board=d.board)
    d.ext = types.SimpleNamespace(runner=FakeRunner(), grimoire=None)
    d.wow = FakeWow()
    d.ops_toolkit = ops_toolkit.OpsToolkit(d, data_dir=tmp_path / "data")
    d.ops_toolkit.runbook.search_dirs = lambda: [tmp_path / "projects"]
    node_project(tmp_path / "projects", "OpenWoW")
    return d


def run(c):
    return asyncio.run(c)


def test_task_types_and_executors(daemon):
    for key in ops_toolkit.TASK_KEYS:
        assert key in daemon.board.executors
        assert br.TASK_TYPES[key].max_level == (3 if key == "runbook.write" else 1)
    assert daemon.ops_toolkit.raid.crash_seen in daemon.wow.on_crash


def test_phrases(daemon):
    q = daemon.quick
    name, steps = q.match("Orion, run OpenWoW")
    assert steps[0].startswith("toolkit:runbook:run|")
    out = run(q.run_steps(steps))
    assert daemon.ext.runner.started[0][0] == "pnpm dev" and "drafted a justfile" in out
    assert daemon.board.pending()[0]["task_type"] == "runbook.write"
    assert q.match("start the server")[1] == ["wow:start"]          # the wow shortcuts still work
    assert q.match("run the server")[1] == ["wow:start"]            # "server" isn't a project
    assert q.match("release Orion 1.1")[1][0] == "toolkit:release:real|orion|1.1"
    assert q.match("dry run release benilla 0.4.2")[1][0] == "toolkit:release:dry|benilla|0.4.2"
    assert q.match("raid night is fridays at 7:30 pm")[1][0] == "toolkit:raid:set|fridays at 7 30 pm"
    assert "Raid night is Fridays at 7:30 pm" in run(q.run_steps(q.match("raid night is fridays at 7:30 pm")[1]))
    assert q.match("What changed in the world DB today?")[1][0] == "toolkit:dbtm:changes|world"
    assert q.match("raid night is Fridays at 7")[1][0] == "toolkit:raid:set|fridays at 7"
    out = run(q.run_steps(q.match("raid night is Fridays at 7")[1]))
    assert out.startswith("Raid night is Fridays at 7 pm")
    assert "Fridays at 7 pm" in run(q.run_steps(q.match("raid status")[1]))
    q.save_command("friday", ["toolkit:raid:status|"])            # saved quick commands can use them too
    with pytest.raises(ValueError):
        q.save_command("bad", ["toolkit:nope:x"])


def test_routes(daemon, tmp_path):
    app = FastAPI()
    app.include_router(ops_toolkit.make_router(lambda: daemon))
    c = TestClient(app)
    projects = c.get("/runbook/projects").json()["projects"]
    assert projects[0]["name"] == "OpenWoW" and projects[0]["steps"]["test"] == "pnpm test"
    learn = c.post("/runbook/learn", json={"project": "openwow"}).json()
    assert learn["files"][0]["path"] == "justfile" and "+test:" in learn["files"][0]["diff"]
    assert c.post("/runbook/run", json={"project": "openwow", "step": "explode"}).status_code == 400
    assert c.post("/runbook/run", json={"project": "nowhere", "step": "test"}).status_code == 400
    r = c.post("/runbook/run", json={"project": "openwow", "step": "test"}).json()
    assert r["state"] == "started" and r["command"] == "pnpm test"
    r = c.post("/raid/config", json={"schedule": "Saturdays at 9 pm", "discord_channel_id": "12345678901"}).json()
    assert r["config"]["weekday"] == 5 and "Saturdays at 9 pm" in r["text"]
    assert c.post("/raid/config", json={"discord_channel_id": "general"}).status_code == 400
    st = c.get("/raid").json()
    assert st["enabled"] and st["schedule"] == "Saturdays at 9 pm"
    assert c.post("/raid/log", json={"text": "Boss down"}).json() == {"ok": True}
    assert c.get("/raid").json()["state"]["log"][-1]["text"] == "Boss down"
    assert c.get("/release").json() == {"releases": []}
    assert c.get("/release/0123456789").status_code == 404
    assert c.post("/dbtm/rollback", json={"snapshot": "nope"}).status_code == 400
    assert c.get("/dbtm/snapshots").json() == {"snapshots": []}


def test_mcp_tools_declare_their_risk():
    from mcp_tools import McpConfig, OrionTools
    tools = OrionTools(cfg=McpConfig(roots=[]), default_project="")
    risks = {n: tools.tools[n].risk for n in ("runbook_list", "runbook_run", "release_plan", "release_start",
                                              "db_snapshot", "db_changes", "db_rollback", "raid_status")}
    assert risks == {"runbook_list": "read", "runbook_run": "run", "release_plan": "read",
                     "release_start": "approval", "db_snapshot": "run", "db_changes": "read",
                     "db_rollback": "approval", "raid_status": "read"}


def test_mcp_runbook_run_goes_through_the_daemon(tmp_path):
    from mcp_tools import McpConfig, OrionTools
    p = node_project(tmp_path / "root", "benilla")

    class Backend:
        def __init__(self):
            self.calls = []

        async def call(self, method, path, body=None, timeout=None):
            self.calls.append((method, path, body))
            return 200, {"state": "started", "message": "Started `pnpm test` in benilla.", "run": {"id": "r1"}}
    be = Backend()
    tools = OrionTools(cfg=McpConfig(roots=[tmp_path / "root"]), backend=be, default_project=str(p))
    text, err = run(tools.call("runbook_run", {"step": "test"}))
    assert not err and "Run r1" in text
    rb_calls = [c for c in be.calls if c[1] == "/runbook/run"]   # the Injection Firewall may ask /firewall/check first
    assert rb_calls and rb_calls[0][2]["project"] == str(p.resolve())
    text, err = run(tools.call("runbook_list", {}))
    assert "test: `pnpm test`" in text and "node 20.11.1" in text
