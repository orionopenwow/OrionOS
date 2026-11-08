"""Orion Toolkit for coding agents: Collision Guard (2), Proof Gate (3) and Baton (1), with temp git repos."""
import asyncio
import json
import os
import subprocess
import sys
import time
import types
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI

import agent_swarm
import agent_toolkit_routes as atr
from approval_queue import ApprovalQueue
from baton import Baton, key_hunks
from collision_guard import CollisionGuard, overlap, parse_porcelain_z, Party
from proof_gate import ProofGate, parse_claim, parse_tests, find_projects

GIT_ENV = {"GIT_AUTHOR_NAME": "Test", "GIT_AUTHOR_EMAIL": "t@example.com",
           "GIT_COMMITTER_NAME": "Test", "GIT_COMMITTER_EMAIL": "t@example.com"}


@pytest.fixture(autouse=True)
def git_identity(monkeypatch):
    for k, v in GIT_ENV.items():
        monkeypatch.setenv(k, v)


def sh(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True).stdout


def make_repo(path: Path, files=None) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", "-b", "main", str(path)], check=True)
    for rel, text in (files or {"README.md": "hello\n"}).items():
        f = path / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(text)
    sh(path, "add", "-A")
    sh(path, "commit", "-q", "-m", "first")
    return path


PY_PROJECT = {
    "calc.py": "def add(a, b):\n    return a + b\n",
    "other.py": "X = 1\n",
    "tests/test_calc.py": "from calc import add\n\n\ndef test_add():\n    assert add(2, 3) == 5\n\n\n"
                          "def test_zero():\n    assert add(0, 0) == 0\n",
    "conftest.py": "import sys, os\nsys.path.insert(0, os.path.dirname(__file__))\n",
}


def agent(aid, cwd, files=(), state="working", name=None, tool="Claude", **kw):
    return {"id": aid, "tool": tool, "name": name or f"{tool} · {Path(str(cwd)).name}", "cwd": str(cwd),
            "state": state, "status": kw.pop("status", "Working"), "reason": kw.pop("reason", ""),
            "files": list(files), "started": kw.pop("started", time.time() - 60),
            "last_activity": kw.pop("last_activity", time.time()), "events": kw.pop("events", []),
            "project": Path(str(cwd)).name, **kw}


class Events:
    def __init__(self):
        self.items = []

    async def __call__(self, ev):
        self.items.append(ev)

    def named(self, name):
        return [e for e in self.items if e.get("event") == name]


# =====================================================================================  Collision Guard
def test_porcelain_parsing_and_overlap_rules():
    raw = " M src/a.py\0R  new.py\0old.py\0?? notes/todo.md\0"
    assert parse_porcelain_z(raw) == ["src/a.py", "new.py", "notes/todo.md"]
    a = Party(id="a", label="A", kind="agent", repo="r", files={"src/a.py", "lib/x.rs"})
    b = Party(id="b", label="B", kind="claim", repo="r", patterns=["src/**"])
    c = Party(id="c", label="C", kind="agent", repo="r", names={"x.rs"})
    assert overlap(a, b) == ["src/a.py"]
    assert overlap(a, c) == ["lib/x.rs"]
    assert overlap(b, c) == []


def test_two_agents_in_one_folder_collide_and_hud_gets_a_red_line(tmp_path):
    repo = make_repo(tmp_path / "proj", {"app.py": "a\n", "ui.tsx": "b\n"})
    (repo / "app.py").write_text("changed\n")
    (repo / "node_modules").mkdir()
    (repo / "node_modules" / "app.py").write_text("ignored\n")
    ev = Events()
    g = CollisionGuard(tmp_path / "data", publish=ev, extra_repos=())
    agents = [agent("claude:1", repo, ["app.py"], started=100), agent("codex:2", repo, ["app.py"], tool="Codex", started=200)]

    async def go():
        first = await g.update(agents)        # seen once: not announced yet (debounced)
        second = await g.update(agents)
        return first, second
    first, second = asyncio.run(go())
    assert first == [] and len(second) == 1
    c = second[0]
    assert {c["a"], c["b"]} == {"claude:1", "codex:2"} and c["files"] == ["app.py"]
    assert c["same_checkout"] and "overwrite each other" in c["message"]
    assert "worktree add" in c["worktree_command"] and "-b orion/codex" in c["worktree_command"]
    assert "Codex" in c["worktree_advice"]          # the agent that started later moves out
    warn = ev.named("collision.warning")
    assert len(warn) == 1 and {warn[0]["a"], warn[0]["b"]} == {"claude:1", "codex:2"}

    snap = {"agents": agents}
    g.decorate(snap)
    assert snap["collisions"][0]["files"] == ["app.py"]

    sh(repo, "checkout", "--", "app.py")             # the clash goes away -> cleared event, no red line
    g._ch_cache.clear()
    asyncio.run(g.update(agents))
    assert ev.named("collision.cleared") and not g.active


def test_agent_in_own_worktree_vs_owner_changes_give_merge_order(tmp_path):
    repo = make_repo(tmp_path / "proj", {"core.py": "x\n", "a.py": "1\n", "b.py": "2\n"})
    wt = tmp_path / "proj-codex"
    sh(repo, "worktree", "add", "-q", str(wt), "-b", "codex-work")
    (wt / "core.py").write_text("codex\n")
    (repo / "core.py").write_text("me\n")
    (repo / "a.py").write_text("me too\n")
    data = tmp_path / "data"
    own_copy = data / "proof" / "wt-1"                 # Orion's own throwaway copies are nobody's work
    sh(repo, "worktree", "add", "-q", "--detach", str(own_copy))
    (own_copy / "core.py").write_text("proof copy\n")
    g = CollisionGuard(data, extra_repos=[repo])
    found = g.conflicts([agent("codex:9", wt, ["core.py"], tool="Codex", state="done")])
    assert len(found) == 1
    c = found[0]
    assert c["a_label"] == "You" and not c["same_checkout"] and "merging them will clash" in c["message"]
    assert "worktree_command" not in c
    assert c["merge_order"][0] == "codex:9" and "it's finished" in c["merge_advice"]


def test_claims_and_is_it_safe_to_start(tmp_path):
    repo = make_repo(tmp_path / "proj", {"src/app.py": "a\n", "docs/x.md": "d\n"})
    g = CollisionGuard(tmp_path / "data", extra_repos=())
    yes = g.check(str(repo), ["src/app.py"], party="Claude", agents=[])
    assert yes["safe"] and yes["answer"].startswith("Yes")
    claim = g.claim("Codex", str(repo), ["src/*.py"])
    no = g.check(str(repo), ["src/app.py"], party="Claude", agents=[])
    assert not no["safe"] and no["answer"].startswith("Not yet") and "Codex" in no["answer"]
    assert "src/app.py" in no["conflicts"][0]["files"]
    other = g.check(str(repo), ["docs/x.md"], party="Claude", agents=[])
    assert other["safe"]
    # claims survive a restart; releasing makes it safe again
    g2 = CollisionGuard(tmp_path / "data", extra_repos=())
    assert [c["id"] for c in g2.claims] == [claim["id"]]
    assert g2.release(claim_id=claim["id"]) == 1
    assert g2.check(str(repo), ["src/app.py"], party="Claude", agents=[])["safe"]
    with pytest.raises(ValueError):
        g2.claim("Codex", str(tmp_path), ["x"])        # not a git repo


def test_claim_by_a_visible_agent_joins_its_robot(tmp_path):
    repo = make_repo(tmp_path / "proj", {"a.py": "1\n", "b.py": "2\n"})
    g = CollisionGuard(tmp_path / "data", extra_repos=())
    g.claim("Codex", str(repo), ["a.py"])
    (repo / "a.py").write_text("claude edit\n")
    found = g.conflicts([agent("claude:1", repo, ["a.py"]), agent("codex:2", repo, [], tool="Codex")])
    assert len(found) == 1 and {found[0]["a"], found[0]["b"]} == {"claude:1", "codex:2"}


# =====================================================================================  Proof Gate
def test_parse_claim_and_tests():
    c = parse_claim("Done. All tests pass and the build works. Changed calc.py and src/ui/App.tsx.")
    assert c["tests_pass"] is True and c["build_ok"] is True and c["files"] == ["calc.py", "src/ui/App.tsx"]
    assert parse_claim("tests don't pass yet")["tests_pass"] is None
    assert parse_claim({"tests_pass": True, "files": ["a.py"]})["files"] == ["a.py"]
    assert parse_claim("", ["x.py"])["files"] == ["x.py"]
    assert parse_tests("FAILED tests/t.py::test_a - assert\n==== 1 failed, 4 passed in 0.1s ====") == \
        {"passed": 4, "failed": 1, "failing": ["tests/t.py::test_a"]}
    cargo = "test a ... ok\ntest b ... FAILED\ntest result: FAILED. 1 passed; 1 failed; 0 ignored\n" \
            "test result: ok. 3 passed; 0 failed; 0 ignored\n"
    assert parse_tests(cargo) == {"passed": 4, "failed": 1, "failing": ["b"]}
    tap = "not ok 2 - adds numbers\n# pass 5\n# fail 1\n"
    assert parse_tests(tap) == {"passed": 5, "failed": 1, "failing": ["adds numbers"]}
    assert parse_tests("Tests:       2 failed, 10 passed, 12 total")["failed"] == 2


def test_projects_are_found_next_to_the_changed_files(tmp_path):
    root = tmp_path / "mono"
    (root / "apps/web").mkdir(parents=True)
    (root / "apps/web/package.json").write_text("{}")
    (root / "apps/api/tests").mkdir(parents=True)
    (root / "apps/api/tests/test_x.py").write_text("")
    (root / "crates/game").mkdir(parents=True)
    (root / "crates/game/Cargo.toml").write_text("")
    found = find_projects(root, ["apps/web/src/a.ts", "apps/api/x.py", "crates/game/src/lib.rs", "README.md"])
    assert found == [("apps/web", "node"), ("apps/api", "python"), ("crates/game", "rust")]


def _worktrees(repo):
    return [ln for ln in sh(repo, "worktree", "list", "--porcelain").splitlines() if ln.startswith("worktree ")]


def test_proof_green_runs_in_a_copy_and_leaves_the_checkout_alone(tmp_path):
    repo = make_repo(tmp_path / "proj", PY_PROJECT)
    (repo / "calc.py").write_text("def add(a, b):\n    return b + a\n")
    (repo / "notes.txt").write_text("new untracked file\n")
    before = sh(repo, "status", "--porcelain")
    gate = ProofGate(tmp_path / "data", auto=False)
    r = asyncio.run(gate.prove(repo=str(repo), claim="All tests pass. Changed calc.py"))
    assert r["verdict"] == "green", r
    assert r["reason"].startswith("Proven:") and "2 tests pass" in r["reason"] and "really changed" in r["reason"]
    assert set(r["changed_files"]) == {"calc.py", "notes.txt"}
    assert [c["name"] for c in r["checks"]] == ["build", "test"]
    assert sh(repo, "status", "--porcelain") == before            # the agent's checkout is untouched
    assert len(_worktrees(repo)) == 1                               # the temporary copy is gone
    assert gate.get(r["id"]) and json.loads((tmp_path / "data" / "proof_results.json").read_text())


def test_proof_amber_when_claimed_tests_fail_and_rulebook_hears(tmp_path, monkeypatch):
    seen = []
    monkeypatch.setitem(sys.modules, "rulebook", types.SimpleNamespace(
        record_violation=lambda rule_id, agent, evidence: seen.append((rule_id, agent, evidence))))
    repo = make_repo(tmp_path / "proj", PY_PROJECT)
    (repo / "calc.py").write_text("def add(a, b):\n    return a - b\n")
    ev = Events()
    gate = ProofGate(tmp_path / "data", publish=ev, auto=False)
    r = asyncio.run(gate.prove_and_announce(repo=str(repo), claim="Fixed it, tests pass. Changed calc.py and other.py"))
    assert r["verdict"] == "amber"
    assert r["reason"].startswith("Said tests pass; 1 fail")
    assert "Said it changed other.py, but it has no changes" in r["reason"]
    assert any("test_add" in t for t in r["failing_tests"])
    assert {s[0] for s in seen} == {"proof.tests-pass-claim", "proof.files-changed-claim"}
    assert r["reported_to_rulebook"]
    assert ev.named("proof.result")[0]["verdict"] == "amber"


def test_proof_of_a_branch_that_is_not_checked_out(tmp_path):
    repo = make_repo(tmp_path / "proj", PY_PROJECT)
    sh(repo, "switch", "-q", "-c", "feature")
    (repo / "calc.py").write_text("def add(a, b):\n    return a + b + 1\n")
    sh(repo, "commit", "-qam", "break it")
    sh(repo, "switch", "-q", "main")
    gate = ProofGate(tmp_path / "data", auto=False)
    r = asyncio.run(gate.prove(target="feature", repo=str(repo)))
    assert r["verdict"] == "amber" and r["changed_files"] == ["calc.py"] and r["reason"].startswith("Tests fail")
    bad = asyncio.run(gate.prove(target="nope", repo=str(repo)))
    assert bad["verdict"] == "amber" and "no branch or agent called 'nope'" in bad["reason"]
    assert len(_worktrees(repo)) == 1


def test_screenshot_is_skipped_gracefully(tmp_path):
    gate = ProofGate(tmp_path / "data", auto=False)
    far = asyncio.run(gate._screenshot({"url": "https://example.com/"}, tmp_path, "x"))
    assert far["skipped"] and "only pages on this computer" in far["note"]
    try:
        import playwright  # noqa: F401
    except ImportError:
        near = asyncio.run(gate._screenshot({"url": "http://127.0.0.1:5173/"}, tmp_path, "x"))
        assert near["skipped"] and "Playwright isn't installed" in near["note"]


def test_robot_turns_green_only_after_proof(tmp_path):
    repo = make_repo(tmp_path / "proj", PY_PROJECT)
    (repo / "calc.py").write_text("def add(a, b):\n    return b + a\n")
    gate = ProofGate(tmp_path / "data", auto=True)
    a = agent("claude:abc", repo, ["calc.py"], state="done", status="Finished")

    async def go():
        started = gate.observe([a])
        snap = {"agents": [dict(a)]}
        gate.decorate(snap)
        checking = snap["agents"][0]
        assert gate.observe([a]) == []           # the same "done" is proven once
        await gate.settle()
        snap2 = {"agents": [dict(a)]}
        gate.decorate(snap2)
        return started, checking, snap2["agents"][0]
    started, checking, after = asyncio.run(go())
    assert started == ["claude:abc"]
    assert checking["state"] == "waiting" and checking["status"] == "Checking its work"
    assert after["state"] == "done" and after["status"] == "Proven" and after["proof"]["verdict"] == "green"
    # working again: the old verdict no longer decorates the robot
    gate.observe([{**a, "state": "working"}])
    assert "claude:abc" not in gate.robots


# =====================================================================================  Baton
def test_handoff_writes_markdown_with_tests_and_next_step(tmp_path):
    repo = make_repo(tmp_path / "proj", PY_PROJECT)
    sh(repo, "switch", "-q", "-c", "fix-calc")
    (repo / "calc.py").write_text("def add(a, b):\n    return a - b\n")
    sh(repo, "commit", "-qam", "Use subtraction for speed")
    (repo / "other.py").write_text("X = 2\n")
    gate = ProofGate(tmp_path / "data", auto=False)
    a = agent("claude:s1", repo, ["calc.py"], events=[{"at": None, "text": "Editing calc.py"}],
              status="Hit its usage limit", reason="Out of usage for now")
    asyncio.run(gate.prove(agent=a, claim="tests pass"))
    b = Baton(tmp_path / "data", proof=gate, agents_fn=lambda: [a], summarise=None)
    rec = asyncio.run(b.handoff("claude:s1", "Codex", goal="Make add() fast", notes="Kept the API the same"))
    path = Path(rec["path"])
    assert path.is_file() and path.parent == repo / ".orion" / "batons" and path.name.endswith("-claude-to-codex.md")
    md = path.read_text()
    for heading in ("# Hand-off: Claude · proj to Codex", "## Goal", "Make add() fast", "## Decisions made",
                    "Use subtraction for speed", "Kept the API the same", "## Files changed", "calc.py",
                    "```diff", "## Tests", "**amber**", "Failing tests:", "test_add", "## What's left",
                    "## Exact next step", "Make the failing test", "Editing calc.py"):
        assert heading in md, heading
    assert rec["prompt"].startswith("You're picking up work from Claude · proj") and rec["relative_path"] in rec["prompt"]
    assert rec["summary_by"] == "template"
    assert b.get(rec["id"])["markdown"] == md and "markdown" not in b.recent()[0]


def test_handoff_uses_the_local_model_summary_when_there_is_one(tmp_path):
    repo = make_repo(tmp_path / "proj")

    async def summarise(facts):
        assert "Repo proj" in facts
        return "It's a tiny repo with one README."
    rec = asyncio.run(Baton(tmp_path / "data", summarise=summarise).handoff("me", "Gemini", repo=str(repo)))
    assert "## Summary\n\nIt's a tiny repo with one README." in rec["markdown"] and rec["summary_by"] == "local model"


def test_key_hunks_are_bounded_and_skip_ignored_folders():
    diff = ("diff --git a/node_modules/x.js b/node_modules/x.js\n@@ -1 +1 @@\n-a\n+b\n"
            "diff --git a/app.py b/app.py\n@@ -1 +1 @@\n-a\n+b\n@@ -5 +5 @@\n-c\n+d\n@@ -9 +9 @@\n-e\n+f\n")
    out = key_hunks(diff)
    assert "node_modules" not in out and "app.py" in out and "1 more hunks" in out


def test_limit_offer_is_a_card_and_approving_writes_the_handoff(tmp_path):
    repo = make_repo(tmp_path / "proj")
    q = ApprovalQueue(tmp_path / "data" / "approvals.json")
    ev = Events()
    b = Baton(tmp_path / "data", publish=ev, approvals=q, summarise=None)
    tired = agent("codex:r1", repo, [], tool="Codex", state="stuck", status="Hit its usage limit",
                  reason="Out of usage for now")

    async def go():
        cards = await b.observe([tired])
        again = await b.observe([tired])
        return cards, again
    cards, again = asyncio.run(go())
    assert len(cards) == 1 and again == []
    item = cards[0]
    assert item["kind"] == "baton_offer" and item["draft"] == "Claude" and "Nothing is started" in item["original"]
    assert ev.named("APPROVAL_REQUIRED")
    assert not (repo / ".orion").exists()               # nothing happens before you approve
    rec = asyncio.run(b.decide(q.get(item["id"]), "edit", "Gemini"))
    assert rec["to"] == "Gemini" and Path(rec["path"]).is_file()
    assert "Mission Control isn't running" in rec["launch_note"] and not rec["launched"]
    done = q.get(item["id"])
    assert done["status"] == "sent" and done["baton_path"] == rec["path"]
    # rejecting another offer writes nothing
    b2 = Baton(tmp_path / "data2", approvals=q, summarise=None)
    card = asyncio.run(b2.observe([{**tired, "id": "codex:r2"}]))[0]
    assert asyncio.run(b2.decide(q.get(card["id"]), "reject")) is None
    assert q.get(card["id"])["status"] == "rejected"


def test_agent_watch_notices_a_usage_limit(tmp_path):
    import agent_watch as aw
    log = tmp_path / "s.jsonl"
    rows = [{"type": "user", "timestamp": "2026-10-16T10:00:00Z", "cwd": "/x", "message": {"content": "go"}},
            {"type": "assistant", "timestamp": "2026-10-16T10:00:05Z",
             "message": {"content": [{"type": "tool_use", "id": "t1", "name": "Edit", "input": {"file_path": "/x/a.py"}}]}},
            {"type": "user", "timestamp": "2026-10-16T10:00:06Z",
             "message": {"content": [{"type": "tool_result", "tool_use_id": "t1"}]}},
            {"type": "assistant", "isApiErrorMessage": True, "timestamp": "2026-10-16T10:00:07Z",
             "message": {"content": [{"type": "text", "text": "Claude AI usage limit reached|1760000000"}]}}]
    log.write_text("\n".join(json.dumps(r) for r in rows))
    info = aw.parse_claude_session(log)
    assert info.hint == "limit"
    assert aw.judge(info, 300) == ("stuck", "Hit its usage limit", "Out of usage for now")
    clog = tmp_path / "rollout.jsonl"
    clog.write_text(json.dumps({"timestamp": "2026-10-16T10:00:00Z", "payload": {"cwd": "/x"}}) + "\n" +
                    json.dumps({"payload": {"type": "error", "message": "You've hit your usage limit."}}))
    assert aw.parse_codex_session(clog).hint == "limit"


# =====================================================================================  swarm hook, routes, MCP tools
def test_swarm_decorators_run_and_a_broken_one_is_harmless():
    watcher = types.SimpleNamespace(swarm=lambda now, launched=None: [
        {"id": "a", "controllable": False}], watching=lambda: [])
    calls = []

    def good(snap):
        calls.append(len(snap["agents"]))
        snap["extra"] = True

    def bad(snap):
        raise RuntimeError("boom")
    agent_swarm.DECORATORS[:] = [bad, good]
    try:
        snap = agent_swarm.AgentSwarm(watcher).list()
        raw = agent_swarm.AgentSwarm(watcher).list(decorate=False)
    finally:
        agent_swarm.DECORATORS.clear()
    assert snap["extra"] and calls == [1] and "extra" not in raw


class RouteHarness:
    def __init__(self, tmp_path, monkeypatch):
        conf = tmp_path / "mcp.json"
        conf.write_text(json.dumps({"roots": [str(tmp_path)]}))
        monkeypatch.setenv("ORION_MCP_CONFIG", str(conf))
        self.events = Events()
        self.q = ApprovalQueue(tmp_path / "data" / "approvals.json")
        daemon = types.SimpleNamespace(_publish_ui=self.events, approvals=self.q, ext=None)
        self.tk = atr.AgentToolkit(daemon, data_dir=tmp_path / "data", extra_repos=())
        self.tk.baton.summarise = None
        app = FastAPI()
        app.include_router(atr.make_router(lambda: self.tk))
        self.app = app

    def client(self):
        return httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url="http://t", timeout=120)


def test_toolkit_routes(tmp_path, monkeypatch):
    h = RouteHarness(tmp_path, monkeypatch)
    repo = make_repo(tmp_path / "proj", PY_PROJECT)
    (repo / "calc.py").write_text("def add(a, b):\n    return b + a\n")

    async def go():
        async with h.client() as c:
            safe = (await c.post("/toolkit/collisions/check", json={"repo": str(repo), "files": ["calc.py"],
                                                                     "party": "Claude"})).json()
            claim = (await c.post("/toolkit/collisions/claim", json={"repo": str(repo), "party": "Codex",
                                                                      "files": ["calc.py"]})).json()
            clash = (await c.post("/toolkit/collisions/check", json={"repo": str(repo), "files": ["calc.py"],
                                                                      "party": "Claude"})).json()
            outside = await c.post("/toolkit/collisions/check", json={"repo": "/etc", "files": ["x"]})
            amap = (await c.get("/toolkit/collisions")).json()
            rel = (await c.post("/toolkit/collisions/release", json={"party": "Codex"})).json()
            proof = (await c.post("/toolkit/proof", json={"repo": str(repo), "claim": "tests pass"})).json()
            one = (await c.get(f"/toolkit/proof/{proof['id']}")).json()
            recent = (await c.get("/toolkit/proof")).json()
            baton = (await c.post("/toolkit/baton", json={"from_agent": "Claude", "to_agent": "Codex",
                                                          "repo": str(repo), "goal": "Tidy calc"})).json()
            batons = (await c.get("/toolkit/batons")).json()
            missing = await c.post("/toolkit/baton", json={"from_agent": "ghost", "to_agent": "Codex"})
            return safe, claim, clash, outside, amap, rel, proof, one, recent, baton, batons, missing
    safe, claim, clash, outside, amap, rel, proof, one, recent, baton, batons, missing = asyncio.run(go())
    assert safe["safe"] is True
    assert claim["claim"]["patterns"] == ["calc.py"] and claim["safe"] is True
    assert clash["safe"] is False and "Codex" in clash["answer"]
    assert outside.status_code == 400
    assert amap["claims"] and rel["released"] == 1
    assert proof["verdict"] == "green" and one["id"] == proof["id"] and recent["results"][0]["id"] == proof["id"]
    assert h.events.named("proof.result")
    assert Path(baton["path"]).is_file() and batons["batons"][0]["id"] == baton["id"]
    assert missing.status_code == 400 and "say which repo" in missing.json()["error"]


def test_toolkit_offer_is_decided_through_the_toolkit(tmp_path, monkeypatch):
    h = RouteHarness(tmp_path, monkeypatch)
    repo = make_repo(tmp_path / "proj")
    tired = agent("claude:t", repo, [], state="stuck", status="Hit its usage limit")
    h.tk._agents = [tired]
    asyncio.run(h.tk.tick())
    item = h.q.pending()[0]
    assert item["kind"] in atr.KINDS
    asyncio.run(h.tk.decide(item, "approve"))
    assert h.q.get(item["id"])["status"] == "sent" and list((repo / ".orion" / "batons").iterdir())
    asyncio.run(h.tk.stop())


def test_mcp_tools_work_without_the_daemon(tmp_path, monkeypatch):
    from mcp_tools import Backend, DaemonUnavailable, McpConfig, OrionTools

    class Down(Backend):
        def __init__(self):
            self.base_url, self.timeout, self._local_runner, self._preload = "http://t", 1, None, None

        async def call(self, method, path, body=None, timeout=None):
            raise DaemonUnavailable("down")
    repo = make_repo(tmp_path / "proj", PY_PROJECT)
    tools = OrionTools(cfg=McpConfig(roots=[tmp_path]), backend=Down(), default_project=str(repo))
    listed = {t["name"]: t for t in tools.list_tools()}
    assert listed["collision_check"]["_meta"]["orion/risk"] == "read"
    assert {listed[n]["_meta"]["orion/risk"] for n in ("collision_claim", "proof_prove", "baton_handoff")} == {"run"}

    async def go():
        check = await tools.call("collision_check", {"files": ["calc.py"], "party": "Claude"})
        claim = await tools.call("collision_claim", {"party": "Codex", "files": ["calc.py"]})
        proof = await tools.call("proof_prove", {"project": str(repo), "claim": "tests pass"})
        baton = await tools.call("baton_handoff", {"from_agent": "Claude", "to_agent": "Codex",
                                                   "project": str(repo)})
        outside = await tools.call("proof_prove", {"project": "/etc"})
        return check, claim, proof, baton, outside
    check, claim, proof, baton, outside = asyncio.run(go())
    assert check == (check[0], False) and check[0].startswith("Yes") and "only git was checked" in check[0]
    assert claim[1] is True and "Nothing was saved" in claim[0]
    assert proof[1] is False and proof[0].startswith("GREEN") and "tests pass" in proof[0]
    assert baton[1] is False and "Hand-off written" in baton[0] and "Prompt for Codex" in baton[0]
    assert outside[1] is True
