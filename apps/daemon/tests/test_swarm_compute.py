"""Compute meter (pricing, per-model accounting, persistence, tips) and the agent swarm list."""
import asyncio
import json
import os
import time
from types import SimpleNamespace

import pytest


def run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


# ---------------------------------------------------------------- compute meter
def test_price_lookup_prefix_local_and_overrides(tmp_path):
    import compute_meter as cm
    prices = cm.load_prices(tmp_path / "missing.json")
    p, known = cm.price_for("claude-sonnet-4-5-20250929", "anthropic", prices)
    assert known and p == {"in": 3.0, "out": 15.0}
    # longest prefix wins: gpt-4o-mini is not priced as gpt-4o
    assert cm.price_for("gpt-4o-mini", "openai", prices)[0]["in"] == 0.15
    assert cm.price_for("llama3.1:8b", "ollama", prices) == ({"in": 0.0, "out": 0.0}, True)
    p, known = cm.price_for("mystery-model", "openai", prices)
    assert not known and p == cm.UNKNOWN_CLOUD_PRICE
    assert cm.cost_of(1_000_000, 500_000, {"in": 3.0, "out": 15.0}) == pytest.approx(10.5)

    f = tmp_path / "prices.json"
    f.write_text(json.dumps({"gpt-4o": {"in": 1.0, "out": 2.0}, "mystery": {"in": 9, "out": 9}, "_note": "x"}))
    over = cm.load_prices(f)
    assert over["gpt-4o"] == {"in": 1.0, "out": 2.0} and cm.price_for("mystery-model", "openai", over)[1]
    f.write_text("{not json")
    assert cm.load_prices(f)["gpt-4o"] == cm.DEFAULT_PRICES["gpt-4o"]   # bad file -> defaults, no crash


def test_meter_per_model_accounting_and_persistence(tmp_path):
    import compute_meter as cm
    store = tmp_path / "meter.json"
    prices = tmp_path / "none.json"
    clock = [1_760_000_000.0]
    m = cm.ComputeMeter(store, prices, clock=lambda: clock[0])
    m.record("claude-sonnet-4-5", "anthropic", 1000, 500)
    m.record("claude-sonnet-4-5", "anthropic", 3000, 1500)
    m.record("llama3.1:8b", "ollama", 5000, 200)
    snap = m.snapshot()
    rows = {r["model"]: r for r in snap["today"]["models"]}
    assert rows["claude-sonnet-4-5"]["calls"] == 2
    assert rows["claude-sonnet-4-5"]["tokens_in"] == 4000 and rows["claude-sonnet-4-5"]["tokens_out"] == 2000
    assert rows["claude-sonnet-4-5"]["cost"] == pytest.approx((4000 * 3 + 2000 * 15) / 1e6)
    assert rows["llama3.1:8b"]["cost"] == 0 and rows["llama3.1:8b"]["local"]
    assert snap["session"]["cost"] == snap["today"]["cost"] and snap["today"]["calls"] == 3
    assert "estimate" in snap["note"]

    # a restart keeps today's totals but starts a fresh session
    m2 = cm.ComputeMeter(store, prices, clock=lambda: clock[0])
    s2 = m2.snapshot()
    assert s2["today"]["calls"] == 3 and s2["session"]["calls"] == 0
    # the next day starts at zero; yesterday shows in history
    clock[0] += 86_400
    m2.record("gpt-4o", "openai", 100, 10)
    s3 = m2.snapshot()
    assert s3["today"]["calls"] == 1 and len(s3["history"]) == 2
    # editing the price file re-prices old numbers
    prices.write_text(json.dumps({"gpt-4o": {"in": 1000, "out": 0}}))
    os.utime(prices, (time.time() + 5, time.time() + 5))
    assert m2.snapshot()["today"]["cost"] == pytest.approx(0.1)
    assert m2.brief()["today_tokens"] == 110


def test_meter_tips_in_plain_words(tmp_path):
    import compute_meter as cm
    m = cm.ComputeMeter(None, tmp_path / "none.json")
    for _ in range(20):
        m.record("gpt-4o", "openai", 400, 5)        # short label-style answers
    tips = cm.tips(m.snapshot()["today"], m.snapshot()["session"])
    assert any("local model" in t for t in tips)
    m2 = cm.ComputeMeter(None, tmp_path / "none.json")
    for _ in range(5):
        m2.record("claude-sonnet-4-5", "anthropic", 20_000, 900)
    assert any("long" in t for t in m2.snapshot()["tips"])
    assert cm.ComputeMeter(None, tmp_path / "none.json").snapshot()["tips"] == []


def test_orchestrator_records_per_model_usage(tmp_path):
    from orchestrator import IntentOrchestrator
    import compute_meter as cm
    o = IntentOrchestrator()
    o.meter = cm.ComputeMeter(None, tmp_path / "none.json")
    o._count_cloud({"input_tokens": 120, "output_tokens": 30}, "input_tokens", "output_tokens",
                   model="claude-sonnet-4-5", provider="anthropic")
    o._count_local({"prompt_eval_count": 50, "eval_count": 7}, "llama3.1:8b")
    o._count_gemini({"usageMetadata": {"promptTokenCount": 10, "candidatesTokenCount": 4}}, "gemini-1.5-flash")
    assert o.usage["cloud_calls"] == 2 and o.usage["local_calls"] == 1
    assert o.usage["tokens_in"] == 180 and o.usage["tokens_out"] == 41
    models = {r["model"]: r for r in o.meter.snapshot()["session"]["models"]}
    assert set(models) == {"claude-sonnet-4-5", "llama3.1:8b", "gemini-1.5-flash"}
    assert models["gemini-1.5-flash"]["provider"] == "gemini" and models["gemini-1.5-flash"]["tokens_out"] == 4
    run(o.aclose())


# ---------------------------------------------------------------- agent swarm
def _claude_log(tmp_path, project_dir, stem, rows):
    proj = tmp_path / ".claude" / "projects" / project_dir
    proj.mkdir(parents=True, exist_ok=True)
    f = proj / f"{stem}.jsonl"
    f.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    return f


def _tool(tid, name, inp, cwd="/work/orion", ts="2026-10-15T10:00:00Z"):
    return {"type": "assistant", "cwd": cwd, "timestamp": ts,
            "message": {"stop_reason": "tool_use", "content": [{"type": "tool_use", "id": tid, "name": name, "input": inp}]}}


def _result(tid, error=False):
    return {"type": "user", "cwd": "/work/orion",
            "message": {"content": [{"type": "tool_result", "tool_use_id": tid, "is_error": error, "content": "SECRET"}]}}


def test_swarm_lists_each_claude_session_with_safe_details(tmp_path, monkeypatch):
    import agent_watch
    monkeypatch.setattr(agent_watch.Path, "home", staticmethod(lambda: tmp_path))
    _claude_log(tmp_path, "-work-orion", "aaa", [
        {"type": "user", "message": {"content": "my private question"}},
        _tool("1", "Edit", {"file_path": "/work/orion/src/App.tsx", "old_string": "SECRET", "new_string": "x"}),
        _result("1"),
        _tool("2", "Bash", {"command": "npm test", "description": "Running the tests"}),
    ])
    _claude_log(tmp_path, "-work-site", "bbb", [_tool("9", "Read", {"file_path": "/work/site/index.html"}, cwd="/work/site")])
    w = agent_watch.AgentWatcher(sources=agent_watch.default_sources(tmp_path))
    agents = {a["id"]: a for a in w.swarm()}
    a = agents["claude:aaa"]
    assert a["name"] == "Claude · orion" and a["state"] == "working" and a["status"] == "Running the tests"
    assert a["files"] == ["App.tsx"] and a["cwd"] == "/work/orion" and not a["controllable"]
    assert [e["text"] for e in a["events"]] == ["Editing App.tsx", "Running the tests"]
    assert "claude:bbb" in agents and agents["claude:bbb"]["project"] == "site"
    blob = json.dumps(list(agents.values()))
    assert "SECRET" not in blob and "private question" not in blob


def test_swarm_states_waiting_failed_done_stuck(tmp_path):
    import agent_watch as aw
    info = aw.SessionInfo(status="Editing App.tsx", hint="pending")
    assert aw.judge(info, 2)[0] == "working"
    assert aw.judge(info, aw.ACTIVE_S + 1)[:2] == ("waiting", "Waiting for your OK")
    shell = aw.SessionInfo(status="Running the tests", hint="pending_shell")
    assert aw.judge(shell, aw.ACTIVE_S + 1)[0] == "working"
    assert aw.judge(shell, aw.SWARM_WAIT_S + 1)[0] == "waiting"
    assert aw.judge(shell, aw.SWARM_STUCK_S + 1)[0] == "stuck"
    assert aw.judge(shell, aw.SWARM_RECENT_S + 1) is None
    err = aw.SessionInfo(hint="error")
    state, _, reason = aw.judge(err, aw.ACTIVE_S + 1)
    assert state == "failed" and reason
    done = aw.SessionInfo(hint="ended")
    assert aw.judge(done, 1)[0] == "done" and aw.judge(done, aw.SWARM_DONE_S + 1) is None

    # parsed from real-looking logs
    f = tmp_path / "s.jsonl"
    f.write_text("\n".join(json.dumps(r) for r in [_tool("1", "Bash", {"command": "pytest"}), _result("1", error=True)]))
    assert aw.parse_claude_session(f).hint == "error"
    f.write_text("\n".join(json.dumps(r) for r in [_tool("1", "Write", {"file_path": "/a/b.py"})]))
    assert aw.parse_claude_session(f).hint == "pending"
    f.write_text("\n".join(json.dumps(r) for r in [_tool("1", "Agent", {"description": "scout"}),
                                                    _tool("2", "Agent", {"description": "build"}), _result("1")]))
    helpers = aw.parse_claude_session(f)
    assert helpers.hint == "pending_helpers" and aw.judge(helpers, aw.SWARM_STUCK_S + 1)[0] == "working"
    f.write_text(json.dumps({"type": "assistant", "message": {"stop_reason": "end_turn",
                                                              "content": [{"type": "text", "text": "done"}]}}))
    assert aw.parse_claude_session(f).hint == "ended"


def test_swarm_codex_files_from_patch_and_pings(tmp_path):
    import agent_watch as aw
    f = tmp_path / "rollout.jsonl"
    rows = [
        {"type": "session_meta", "timestamp": "2026-10-15T09:00:00Z", "payload": {"cwd": "/work/game", "instructions": "SECRET"}},
        {"type": "response_item", "payload": {"type": "custom_tool_call", "name": "apply_patch", "call_id": "c1",
                                              "input": "*** Begin Patch\n*** Update File: src/main.rs\n+SECRET\n*** End Patch"}},
    ]
    f.write_text("\n".join(json.dumps(r) for r in rows))
    info = aw.parse_codex_session(f)
    assert info.cwd == "/work/game" and info.files == ["main.rs"] and info.hint == "pending_shell"
    assert "SECRET" not in json.dumps(info.__dict__)

    w = aw.AgentWatcher(sources=[])
    w.ping("Claude web", "Editing App.tsx", "orion")
    s = w.swarm()
    assert s[0]["name"] == "Claude web · orion" and s[0]["state"] == "working"
    w.ping("Claude web", state="done")
    s = w.swarm()
    assert s[0]["state"] == "done"
    assert w.swarm(time.time() + aw.SWARM_DONE_S + 1) == []


def test_swarm_controls_only_for_orion_started_agents(tmp_path, monkeypatch):
    import agent_watch as aw
    from agent_swarm import AgentSwarm, EXTERNAL_NOTE, agent_tool

    assert agent_tool("claude -p 'fix the tests'") == "Claude"
    assert agent_tool("npx @openai/codex exec x") == "Codex"
    assert agent_tool("npm test") is None

    monkeypatch.setattr(aw.Path, "home", staticmethod(lambda: tmp_path))
    _claude_log(tmp_path, "-work-ext", "ext", [_tool("1", "Read", {"file_path": "/x/y.py"}, cwd=str(tmp_path))])
    stopped = []

    class Runner:
        def __init__(self):
            self.runs = {
                "run-1": SimpleNamespace(id="run-1", command="claude -p go", cwd=str(tmp_path / "mine"), label="go",
                                         started_at=time.time(), ended_at=None, exit_code=None,
                                         proc=SimpleNamespace(pid=999999)),
                "run-2": SimpleNamespace(id="run-2", command="npm test", cwd=None, label="tests",
                                         started_at=time.time(), ended_at=None, exit_code=None, proc=None),
            }

        async def stop(self, run_id):
            stopped.append(run_id)
            return True

    (tmp_path / "mine").mkdir()
    sw = AgentSwarm(aw.AgentWatcher(sources=aw.default_sources(tmp_path)), Runner())
    agents = {a["id"]: a for a in sw.list()["agents"]}
    assert set(agents) == {"claude:ext", "run:run-1"}
    ext, mine = agents["claude:ext"], agents["run:run-1"]
    assert not ext["controllable"] and ext["control_note"] == EXTERNAL_NOTE and ext["can_open"]
    assert mine["controllable"] and mine["name"] == "Claude · mine"

    ok, msg = run(sw.act("claude:ext", "stop"))
    assert not ok and msg == EXTERNAL_NOTE and stopped == []
    ok, _ = run(sw.act("run:run-1", "stop"))
    assert ok and stopped == ["run-1"]
    ok, _ = run(sw.act("nope", "stop"))
    assert not ok


def test_http_routes_compute_and_swarm():
    from fastapi.testclient import TestClient
    import main
    from conftest import daemon_client
    with daemon_client(main.app) as c:
        snap = c.get("/compute").json()
        assert {"today", "session", "history", "tips", "note"} <= set(snap)
        assert "compute" in c.get("/health").json()["telemetry"]
        body = c.get("/agents/swarm").json()
        assert isinstance(body["agents"], list) and "watching" in body
        r = c.post("/agents/swarm/nobody/stop")
        assert r.status_code == 400 and r.json()["ok"] is False
