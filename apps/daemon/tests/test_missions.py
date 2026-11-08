"""Mission Control (Phase 3 "Fleet"): missions in worktrees, adapters, router, two-model review, race, swarm,
speculative patches, signed merge, HTTP routes, Baton hook and swarm robots. Agent CLIs are fake scripts in a temp
folder; nothing calls a real agent or the network."""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

import mission_routing
import missions
from missions import ADAPTERS, MissionControl, MissionError, build_argv

PY = sys.executable
POSIX = sys.platform != "win32"
pytestmark = pytest.mark.skipif(not POSIX, reason="fake CLIs are POSIX scripts")


# ------------------------------------------------------------------ helpers
def sh(cwd: Path, *args: str) -> str:
    r = subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false",
                        "-C", str(cwd), *args], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return r.stdout


def make_repo(tmp: Path) -> Path:
    repo = tmp / "proj"
    repo.mkdir()
    sh(repo, "init", "-q", "-b", "main")
    (repo / "README.md").write_text("hello\n")
    sh(repo, "add", "-A")
    sh(repo, "commit", "-q", "-m", "init")
    return repo


CLAUDE_WORK = r'''
import json, sys, os, time
args = sys.argv[1:]
if "plan" in args:   # review mode
    print(json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": "Looks right.\nVERDICT: approve"}]}}))
    print(json.dumps({"type": "result", "result": "Looks right. VERDICT: approve", "total_cost_usd": 0.03,
                      "usage": {"input_tokens": 100, "output_tokens": 20}}))
    sys.exit(0)
open("hello.txt", "w").write("made by the fake agent\n")
print(json.dumps({"type": "system", "subtype": "init", "model": "claude-sonnet-4-5"}))
print(json.dumps({"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "Write",
                  "input": {"file_path": "hello.txt"}}]}}))
print(json.dumps({"type": "result", "result": "Added hello.txt. Tests pass.", "total_cost_usd": 0.12,
                  "usage": {"input_tokens": 1000, "output_tokens": 200}}))
'''

CODEX_REVIEW = r'''
import json, sys
args = sys.argv[1:]
if "read-only" in args:
    print(json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": "Fine by me. VERDICT: approve"}}))
    print(json.dumps({"type": "turn.completed", "usage": {"input_tokens": 500, "output_tokens": 50}}))
    sys.exit(0)
open("codex.txt", "w").write("codex was here\n")
print(json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": "Wrote codex.txt"}}))
print(json.dumps({"type": "turn.completed", "usage": {"input_tokens": 2000, "output_tokens": 300}}))
'''

SPENDER = r'''
import json, sys, time
for i in range(400):
    print(json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": f"step {i}"}],
                      "usage": {"input_tokens": 200000, "output_tokens": 50000}}}), flush=True)
    time.sleep(0.05)
'''

SLEEPER = r'''
import sys, time
print("thinking", flush=True)
time.sleep(60)
'''

NOOP = r'''
print("nothing to do")
'''

SECRET = r'''
key = "AKIA" + "Q7XK2M9PLR4TB8WZ"
open("config.py", "w").write("AWS_KEY = '" + key + "'\n")
print("done")
'''


def make_cli(bin_dir: Path, name: str, body: str) -> Path:
    bin_dir.mkdir(exist_ok=True)
    p = bin_dir / name
    p.write_text(f"#!{PY}\n{body}")
    p.chmod(0o755)
    return p


def which_in(bin_dir: Path):
    def which(name: str):
        p = bin_dir / name
        return str(p) if p.exists() else None
    return which


class FakeProof:
    def __init__(self, verdict: str = "green") -> None:
        self.verdict = verdict
        self.calls: list[tuple[str, str]] = []

    async def prove(self, target: str = "", repo: str = "", claim=None, **kw):
        self.calls.append((target, repo))
        return {"id": "p1", "verdict": self.verdict, "reason": "2 passed" if self.verdict == "green" else "1 fails",
                "failing_tests": [] if self.verdict == "green" else ["test_x"]}


class FakeMeter:
    def __init__(self) -> None:
        self.rows = []

    def record(self, model, provider, tin=0, tout=0):
        self.rows.append((model, provider, tin, tout))


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.delenv("ORION_AGENT_WORK_DIR", raising=False)
    monkeypatch.setenv("ORION_ROUTING_FILE", str(missions.mission_routing.BUILTIN))
    from approval_queue import ApprovalQueue
    repo = make_repo(tmp_path)
    bins = tmp_path / "bin"
    bins.mkdir()

    def mk(**kw):
        kw.setdefault("which", which_in(bins))
        kw.setdefault("proof", FakeProof())
        kw.setdefault("approvals", ApprovalQueue(tmp_path / "approvals.json"))
        kw.setdefault("tick_s", 0.05)
        return MissionControl(tmp_path / "data", default_repo=repo, **kw)
    return {"repo": repo, "bins": bins, "tmp": tmp_path, "mk": mk}


async def wait_for(pred, timeout: float = 20.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        await asyncio.sleep(0.05)
    raise AssertionError("timed out waiting")


async def settle(mc: MissionControl, mid: str, states=("review", "done", "failed", "stopped", "needs_you")):
    await wait_for(lambda: mc.get(mid)["state"] in states and mid not in mc._tasks)
    await asyncio.sleep(0.1)
    return mc.get(mid)


# ------------------------------------------------------------------ router
def test_routing_file_is_versioned_and_routes_by_strength():
    rules = mission_routing.load_routing(mission_routing.BUILTIN)
    assert isinstance(rules["version"], int) and rules["version"] >= 1
    assert mission_routing.classify("Fix the crash in the parser", rules) == "bugfix"
    assert mission_routing.classify("Design the zone lore and quest flow", rules) == "architecture"
    assert mission_routing.classify("anything", rules, "website") == "website"
    p = mission_routing.pick("architecture", ["claude", "codex"], rules)
    assert p["agent"] == "claude" and p["cost_cap_usd"] == 3.0
    p = mission_routing.pick("bugfix", ["claude"], rules)   # codex missing -> fallback, says so
    assert p["agent"] == "claude" and "fallback" in p["why"]
    assert mission_routing.pick("bugfix", [], rules)["agent"] is None
    assert mission_routing.reviewer("claude", ["claude", "codex"], rules) == "codex"
    assert mission_routing.reviewer("claude", ["claude"], rules) == "claude"
    assert mission_routing.reviewer("claude", [], rules) is None


def test_bad_routing_override_falls_back(tmp_path):
    bad = tmp_path / "routing.json"
    bad.write_text(json.dumps({"tasks": {}}))   # no version
    rules = mission_routing.load_routing(bad)
    assert rules["version"] >= 1 and rules["source"].endswith("routing.json") and rules["source"] != str(bad)


# ------------------------------------------------------------------ adapters
def test_adapter_command_templates():
    assert build_argv(ADAPTERS["claude"], "/x/claude", "do it", None) == [
        "/x/claude", "-p", "do it", "--output-format", "stream-json", "--verbose", "--permission-mode", "acceptEdits"]
    assert build_argv(ADAPTERS["claude"], "claude", "p", "claude-opus-4-5", "review")[-4:] == [
        "--permission-mode", "plan", "--model", "claude-opus-4-5"]
    assert build_argv(ADAPTERS["codex"], "codex", "p", "gpt-5") == ["codex", "exec", "--full-auto", "--json", "-m",
                                                                    "gpt-5", "p"]
    assert build_argv(ADAPTERS["codex"], "codex", "p", None, "review") == ["codex", "exec", "--sandbox", "read-only",
                                                                           "--json", "p"]
    assert build_argv(ADAPTERS["gemini"], "gemini", "p", None) == ["gemini", "--approval-mode", "auto_edit", "-p", "p"]
    with pytest.raises(ValueError):
        build_argv(ADAPTERS["antigravity"], "antigravity", "p", None)
    assert missions.agent_key("Claude Code") == "claude" and missions.agent_key("Codex") == "codex"
    assert missions.agent_key("gemini cli") == "gemini" and missions.agent_key("nope") is None


def test_agent_env_keeps_only_its_own_login_and_blocks_push():
    fake_key = "sk-" + "ant-" + "x" * 10
    src = {"PATH": "/bin", "HOME": "/h", "ANTHROPIC_API_KEY": fake_key, "OPENAI_API_KEY": "o" * 8,
           "ORION_API_TOKEN": "t", "GITHUB_TOKEN": "g", "SSH_AUTH_SOCK": "/tmp/agent", "LANG": "C"}
    env = missions.agent_env(ADAPTERS["claude"], src)
    assert env["ANTHROPIC_API_KEY"] == fake_key and env["PATH"] == "/bin" and env["HOME"] == "/h"
    assert "OPENAI_API_KEY" not in env and "ORION_API_TOKEN" not in env and "GITHUB_TOKEN" not in env
    assert "SSH_AUTH_SOCK" not in env and env["GIT_TERMINAL_PROMPT"] == "0"
    n = int(env["GIT_CONFIG_COUNT"])
    assert {env[f"GIT_CONFIG_VALUE_{i}"] for i in range(n)} >= {"https://", "git@", "ssh://"}
    assert all("pushInsteadOf" in env[f"GIT_CONFIG_KEY_{i}"] for i in range(n))


def test_push_from_a_mission_env_fails(tmp_path):
    repo = make_repo(tmp_path)
    sh(repo, "remote", "add", "origin", "https://example.invalid/x.git")
    env = missions.agent_env(ADAPTERS["claude"], dict(os.environ))
    r = subprocess.run(["git", "-C", str(repo), "push", "origin", "main"], capture_output=True, text=True, env=env,
                       timeout=30)
    assert r.returncode != 0 and "orion-mission-no-push" in (r.stderr + r.stdout)


def test_parsers():
    ev = missions.parse_claude_line(json.dumps({"type": "result", "result": "ok", "total_cost_usd": 0.5,
                                                "usage": {"input_tokens": 10, "output_tokens": 5},
                                                "permission_denials": [{"tool_name": "Bash"}]}))
    assert ev["cost_usd"] == 0.5 and ev["final"] and ev["tokens_in"] == 10 and "Bash" in ev["text"]
    assert missions.parse_claude_line("plain text")["text"] == "plain text"
    ev = missions.parse_codex_line(json.dumps({"type": "turn.completed", "usage": {"input_tokens": 7, "output_tokens": 3}}))
    assert ev["tokens_in_step"] == 7 and ev["tokens_out_step"] == 3
    ev = missions.parse_codex_line(json.dumps({"msg": {"type": "agent_message", "message": "hi"}}))
    assert ev["text"] == "hi"
    assert missions.parse_verdict("blah\nVERDICT: changes\n") == "changes"
    assert missions.parse_verdict("**VERDICT: approve**") == "approve"
    assert missions.parse_verdict("no idea") == "unknown"


# ------------------------------------------------------------------ the whole flow
def test_mission_runs_reviews_and_merges_only_after_signature(env):
    from boardroom import Boardroom, packet_hash
    make_cli(env["bins"], "claude", CLAUDE_WORK)
    make_cli(env["bins"], "codex", CODEX_REVIEW)
    board = Boardroom(env["tmp"] / "board", approvals=None)
    meter = FakeMeter()
    mc = env["mk"](board=board, meter=meter)
    missions.register_task_type()
    board.executors[missions.TASK_TYPE] = mc.exec_merge
    board.on_decided.append(mc.on_decided)
    repo = env["repo"]

    async def go():
        mc.start()
        m = mc.create("Design a hello file", agent=None, budget_usd=1.0, description="Add hello.txt")
        assert m["agent"] == "claude" and m["state"] == "queued" and m["branch"].startswith("orion/mission-")
        m = await settle(mc, m["id"])
        assert m["state"] == "review", m["summary"]
        assert m["changed_files"] == ["hello.txt"]
        assert m["checks"]["verdict"] == "green"
        assert m["review"]["reviewer"] == "Codex" and m["review"]["verdict"] == "approve"
        assert m["cost_usd"] >= 0.12 and m["cost_estimated"] is True   # Claude reported; the review is an estimate
        assert "Reported by" in m["cost_note"] or "Estimate" in m["cost_note"]
        assert meter.rows and meter.rows[0][1] == "anthropic"
        assert not (repo / "hello.txt").exists()                 # your checkout untouched
        card = mc.approvals.get(m["card_id"])
        assert card["kind"] == missions.CARD_KIND and "hello.txt" in card["diff"]
        log = mc.read_log(m["id"])
        assert any("Write" in ln for ln in log["lines"])
        # approve only asks for the signed merge
        res = mc.act(m["id"], "approve_review")
        assert res["ok"]
        await wait_for(lambda: mc.get(m["id"]).get("decision_id"))
        assert not (repo / "hello.txt").exists() and mc.get(m["id"])["state"] == "review"
        d = board.get(mc.get(m["id"])["decision_id"])
        assert d["status"] == "pending" and d["task_type"] == missions.TASK_TYPE
        await board.verdict(d["id"], "approve", packet_hash(d["packet"]), signer="test")
        m = mc.get(m["id"])
        assert m["state"] == "done" and m["merge"] == "merged"
        assert (repo / "hello.txt").read_text() == "made by the fake agent\n"   # merged into main, locally
        assert "Merge mission" in sh(repo, "log", "-1", "--format=%s")
        assert m["worktree"] is None
        await mc.stop()
    asyncio.run(go())


def test_rejected_merge_throws_work_away(env):
    from boardroom import Boardroom, packet_hash
    make_cli(env["bins"], "claude", CLAUDE_WORK)
    board = Boardroom(env["tmp"] / "board")
    mc = env["mk"](board=board)
    missions.register_task_type()
    board.executors[missions.TASK_TYPE] = mc.exec_merge
    board.on_decided.append(mc.on_decided)

    async def go():
        mc.start()
        m = await settle(mc, mc.create("Add hello", agent="claude")["id"])
        assert m["state"] == "review"
        assert m["review"]["reviewer"] == "Claude Code" and m["review"]["same_tool"]   # only one tool installed
        mc.act(m["id"], "approve_review")
        await wait_for(lambda: mc.get(m["id"]).get("decision_id"))
        d = board.get(mc.get(m["id"])["decision_id"])
        await board.verdict(d["id"], "reject", packet_hash(d["packet"]), signer="test")
        assert mc.get(m["id"])["state"] == "stopped"
        assert m["branch"] not in sh(env["repo"], "branch", "--list")
        await mc.stop()
    asyncio.run(go())


def test_approve_without_boardroom_refuses_and_reject_cleans_up(env):
    make_cli(env["bins"], "claude", CLAUDE_WORK)
    mc = env["mk"]()

    async def go():
        mc.start()
        m = await settle(mc, mc.create("Add hello", agent="claude")["id"])
        res = mc.act(m["id"], "approve_review")
        assert not res["ok"] and "signature" in res["message"]
        wt = Path(m["worktree"])
        assert wt.is_dir()
        res = mc.act(m["id"], "reject")
        assert res["ok"] and mc.get(m["id"])["state"] == "stopped" and not wt.exists()
        assert mc.approvals.get(m["card_id"])["status"] == "rejected"
        assert mc.act(m["id"], "approve_review")["ok"] is False
        await mc.stop()
    asyncio.run(go())


def test_budget_cap_stops_the_mission(env):
    make_cli(env["bins"], "claude", SPENDER)
    mc = env["mk"]()

    async def go():
        mc.start()
        m = await settle(mc, mc.create("Spend", agent="claude", budget_usd=0.5)["id"])
        assert m["state"] == "stopped" and "budget cap" in m["summary"]
        assert m["cost_usd"] >= 0.5 and m["cost_estimated"]
        await mc.stop()
    asyncio.run(go())


def test_time_limit_and_stop(env):
    make_cli(env["bins"], "gemini", SLEEPER)
    mc = env["mk"]()

    async def go():
        mc.start()
        m1 = mc.create("Slow", agent="gemini")
        mc.get(m1["id"])["time_limit_min"] = 0.02     # about a second
        m = await settle(mc, m1["id"])
        assert m["state"] == "stopped" and "out of time" in m["summary"]
        m2 = mc.create("Slow again", agent="gemini")
        await wait_for(lambda: m2["id"] in mc._procs)
        assert mc.act(m2["id"], "pause")["ok"]
        assert mc.get(m2["id"])["state"] == "needs_you" and mc.get(m2["id"])["paused"]
        assert mc.act(m2["id"], "resume")["ok"] and mc.get(m2["id"])["state"] == "running"
        assert mc.act(m2["id"], "stop")["ok"]
        m = await settle(mc, m2["id"])
        assert m["state"] == "stopped" and "You stopped it" in m["summary"]
        assert mc.act(m2["id"], "stop")["ok"] is False
        await mc.stop()
    asyncio.run(go())


def test_not_installed_watch_only_no_changes_and_secret(env):
    make_cli(env["bins"], "gemini", NOOP)
    make_cli(env["bins"], "claude", SECRET)
    mc = env["mk"]()

    async def go():
        mc.start()
        m = await settle(mc, mc.create("x", agent="codex")["id"])
        assert m["state"] == "failed" and "isn't installed" in m["summary"]
        m = await settle(mc, mc.create("x", agent="antigravity")["id"])
        assert m["state"] == "needs_you" and "watch-only" in m["summary"]
        m = await settle(mc, mc.create("Nothing", agent="gemini")["id"])
        assert m["state"] == "done" and "without changing" in m["summary"]
        assert m["branch"] not in sh(env["repo"], "branch", "--list")
        m = await settle(mc, mc.create("Leaky", agent="claude")["id"])
        assert m["state"] == "needs_you" and "secret" in m["summary"] and "config.py" in m["summary"]
        assert sh(env["repo"], "rev-parse", m["branch"]).strip() == sh(env["repo"], "rev-parse", "main").strip()
        await mc.stop()
    asyncio.run(go())


def test_create_validation(env):
    mc = env["mk"]()
    with pytest.raises(MissionError):
        mc.create("x", repo=str(env["tmp"]))          # not a repo
    with pytest.raises(MissionError):
        mc.create("x")                                 # nothing installed -> plain reason
    with pytest.raises(MissionError):
        mc.create("x", agent="robot9000")
    with pytest.raises(MissionError):
        mc.create("", agent="claude")
    m = mc.create("", agent="claude", template="bugfix", budget_usd=999, time_limit_min=0)
    assert m["title"] == "Fix this bug" and m["budget_usd"] == missions.BUDGET_MAX
    assert mc.get(m["id"])["task"] == "bugfix" and "test" in mc.get(m["id"])["description"]


# ------------------------------------------------------------------ race, swarm, speculative
def test_race_mode_one_card_pick_best(env):
    from boardroom import Boardroom
    make_cli(env["bins"], "claude", CLAUDE_WORK)
    make_cli(env["bins"], "codex", CODEX_REVIEW)
    board = Boardroom(env["tmp"] / "board")
    mc = env["mk"](board=board, max_parallel=2)
    missions.register_task_type()

    async def go():
        mc.start()
        r = mc.create("Build a thing", race=2)
        ids = r["race"]
        assert len(ids) == 2 and {mc.get(i)["agent"] for i in ids} == {"claude", "codex"}
        for i in ids:
            await settle(mc, i)
        await asyncio.sleep(0.3)
        cards = [c for c in mc.approvals.pending() if c["kind"] == missions.RACE_CARD_KIND]
        assert len(cards) == 1 and set(cards[0]["candidates"]) == set(ids)
        winner = cards[0]["candidates"][0]
        loser = next(i for i in ids if i != winner)
        assert mc.act(winner, "approve_review")["ok"]
        assert mc.get(loser)["state"] == "stopped" and "Another racer" in mc.get(loser)["summary"]
        await wait_for(lambda: mc.get(winner).get("decision_id"))
        await mc.stop()
    asyncio.run(go())


def test_swarm_concurrency_and_cost_cap(env):
    make_cli(env["bins"], "gemini", SLEEPER)
    mc = env["mk"]()

    async def go():
        mc.start()
        sw = mc.spawn_swarm("Many", tasks=["a", "b", "c"], agent="gemini", concurrency=1, budget_usd=1.0)
        assert len(sw["missions"]) == 3 and sw["concurrency"] == 1
        await wait_for(lambda: len(mc._procs) == 1)
        await asyncio.sleep(0.4)
        assert sum(1 for i in sw["members"] if mc.get(i)["state"] == "running") == 1
        mc.get(sw["members"][0])["cost_usd"] = 1.5        # pretend it spent the whole cap
        mc.tick()
        for i in sw["members"]:
            await settle(mc, i)
        assert all(mc.get(i)["state"] == "stopped" for i in sw["members"])
        assert "cost cap" in mc.get(sw["members"][2])["summary"]
        with pytest.raises(MissionError):
            mc.spawn_swarm("Too many", size=51, agent="gemini")
        await mc.stop()
    asyncio.run(go())


def test_speculative_patches_are_tested_in_throwaway_worktrees(env):
    proof = FakeProof()
    mc = env["mk"](proof=proof)
    repo = env["repo"]
    good = ("diff --git a/README.md b/README.md\n--- a/README.md\n+++ b/README.md\n@@ -1 +1 @@\n-hello\n+hello world\n")
    bad = ("diff --git a/README.md b/README.md\n--- a/README.md\n+++ b/README.md\n@@ -1 +1 @@\n-nope\n+x\n")

    async def go():
        res = await mc.speculate(str(repo), [bad, good])
        assert res[0]["verdict"] == "green" and res[1]["verdict"] == "amber" and "doesn't apply" in res[1]["reason"]
    asyncio.run(go())
    assert proof.calls and proof.calls[0][0].startswith("orion/spec-")
    assert (repo / "README.md").read_text() == "hello\n"
    assert "orion/spec-" not in sh(repo, "branch", "--list")
    assert len(sh(repo, "worktree", "list").splitlines()) == 1


# ------------------------------------------------------------------ persistence, module API, Baton, robots
def test_restart_marks_running_missions_needs_you(env):
    make_cli(env["bins"], "claude", CLAUDE_WORK)
    mc = env["mk"]()
    m = mc.create("x", agent="claude")
    mc.get(m["id"])["state"] = "running"
    mc._save()
    mc2 = env["mk"]()
    assert mc2.get(m["id"])["state"] == "needs_you" and "restarted" in mc2.get(m["id"])["summary"]
    assert mc2.act(m["id"], "resume")["ok"] and mc2.get(m["id"])["state"] == "queued"


def test_module_api_shape(env):
    make_cli(env["bins"], "claude", CLAUDE_WORK)
    mc = env["mk"]()
    missions.install(mc)
    try:
        m = missions.create_mission("Hello", repo=str(env["repo"]), agent="claude", budget_usd=0.5, source="voice")
        rows = missions.list_missions()
        assert rows[0]["id"] == m["id"]
        for k in ("id", "title", "repo", "agent", "state", "cost_usd", "budget_usd", "started", "branch", "summary"):
            assert k in rows[0]
        assert rows[0]["budget_usd"] == 0.5 and mc.get(m["id"])["source"] == "voice"
        assert missions.act(m["id"], "pause")["ok"]               # queued -> held
        assert missions.act(m["id"], "nonsense")["ok"] is False
        assert missions.act("m-nope", "stop")["ok"] is False
    finally:
        missions.install(None)


def test_baton_launches_through_mission_control(env):
    make_cli(env["bins"], "codex", CODEX_REVIEW)
    mc = env["mk"]()

    async def go():
        note = await mc.launch("Codex", str(env["repo"]), "Pick up the work")
        assert "mission" in note
        m = mc.list()[0]
        assert m["agent"] == "codex" and m["source"] == "baton" and m["description"] == "Pick up the work"
        with pytest.raises(MissionError):
            await mc.launch("Antigravity", str(env["repo"]), "x")
    asyncio.run(go())
    import baton
    missions.install(mc)
    try:
        assert baton.mission_launcher() is None       # not started yet
    finally:
        missions.install(None)


def test_swarm_decorator_adds_cost_and_mission_robots(env):
    mc = env["mk"]()
    rec = mc._new_record("Robot", env["repo"], "claude", None, 1.0, 10, "hud", "", "default", "")
    rec.update(state="running", worktree=str(env["tmp"]), cost_usd=0.25, cost_estimated=True)
    mc.missions[rec["id"]] = rec
    rec2 = mc._new_record("Other", env["repo"], "codex", None, 1.0, 10, "hud", "", "default", "")
    rec2.update(state="needs_you", cost_usd=0.1)
    mc.missions[rec2["id"]] = rec2
    snap = {"agents": [{"id": "claude:abc", "cwd": str(env["tmp"])}, {"id": "codex:x", "cwd": "/elsewhere"}]}
    mc.decorate(snap)
    a = snap["agents"][0]
    assert a["mission_id"] == rec["id"] and a["cost_usd"] == 0.25 and a["cost_estimated"] and a["controllable"]
    assert snap["agents"][1]["cost_usd"] is None
    extra = [x for x in snap["agents"] if x["id"] == f"mission:{rec2['id']}"]
    assert extra and extra[0]["state"] == "waiting" and extra[0]["cost_usd"] == 0.1


# ------------------------------------------------------------------ HTTP
def test_http_routes(env, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    import missions_routes
    make_cli(env["bins"], "claude", CLAUDE_WORK)
    mc = env["mk"]()

    class D:
        mission_control = mc
    app = FastAPI()
    app.include_router(missions_routes.make_router(lambda: D))
    c = TestClient(app)
    r = c.get("/missions")
    assert r.status_code == 200
    body = r.json()
    assert [t["label"] for t in body["templates"]] == ["New website", "New game prototype", "Fix this bug",
                                                       "Add a feature to Orion"]
    assert any(a["key"] == "claude" and a["installed"] for a in body["agents"])
    assert body["routing"]["version"] >= 1
    r = c.post("/missions", json={"title": "Do a thing", "agent": "claude", "budget_usd": 0.3})
    assert r.status_code == 200, r.text
    mid = r.json()["id"]
    assert c.get(f"/missions/{mid}").json()["title"] == "Do a thing"
    assert c.get(f"/missions/{mid}/log").json()["lines"]
    r = c.post(f"/missions/{mid}/approve_review")
    assert r.status_code == 400                       # not in review: refused, nothing merged
    # a mission in review over HTTP still can't merge without the signed boardroom flow
    mc.get(mid)["state"] = "review"
    r = c.post(f"/missions/{mid}/approve_review")
    assert r.status_code == 400 and "signature" in r.json()["message"]
    assert c.post(f"/missions/{mid}/explode").status_code == 400
    assert c.get("/missions/m-nope").status_code == 404
    r = c.post("/missions", json={"title": "x", "repo": "/definitely/not/allowed"})
    assert r.status_code == 400
    assert c.post("/missions", json={"title": ""}).status_code == 400


def test_decide_card_approve_asks_and_reject_rejects(env):
    import missions_routes
    make_cli(env["bins"], "claude", CLAUDE_WORK)
    mc = env["mk"]()

    class D:
        mission_control = mc
    m = mc.create("x", agent="claude")
    rec = mc.get(m["id"])
    rec["state"] = "review"
    card = mc._add_card(missions.CARD_KIND, "Review", "text", rec["branch"], mission_id=rec["id"])
    rec["card_id"] = card["id"]
    asyncio.run(missions_routes.decide_card(D, card, "approve"))
    assert mc.approvals.get(card["id"])["status"] == "pending"     # no boardroom: refused, card stays, error shown
    assert "signature" in mc.approvals.get(card["id"])["error"]
    asyncio.run(missions_routes.decide_card(D, card, "reject"))
    assert mc.approvals.get(card["id"])["status"] == "rejected" and rec["state"] == "stopped"
