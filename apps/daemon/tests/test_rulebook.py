"""One Rulebook (toolkit tool 6): rules written once in .orion/rules.md, synced to every agent's file."""
import asyncio
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

import rulebook as rb
from mcp_tools import Backend, DaemonUnavailable, McpConfig, OrionTools

DAEMON_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = DAEMON_DIR.parents[1]
NODE = shutil.which("node")

SEED = """# Test rules

<!-- editing notes for humans: not copied into the agents' files -->

Intro line for every agent.

## Approvals
- [no-push] Never push without asking.
- [ask-first] Anything public waits for approval,
  including releases.

## Secrets
- [no-secrets] Never commit secrets.
"""


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "proj"
    (root / ".orion").mkdir(parents=True)
    (root / ".orion" / "rules.md").write_text(SEED)
    return root


def test_parse():
    rules = rb.parse(SEED)
    assert [(r.id, r.section) for r in rules] == [("no-push", "Approvals"), ("ask-first", "Approvals"),
                                                  ("no-secrets", "Secrets")]
    assert rules[1].text == "Anything public waits for approval, including releases."


def test_sync_writes_every_target_and_is_idempotent(repo):
    res = rb.sync(repo)
    assert res["changed"] == sorted(t for t, _ in rb.TARGETS)
    for target, agent in rb.TARGETS:
        text = (repo / target).read_text()
        assert rb.GENERATED_MARK in text and "edit .orion/rules.md" in text
        assert "- [no-push] Never push without asking." in text
        assert "editing notes for humans" not in text
        assert f"## Notes for {agent} only" in text and rb.KEEP_START in text
    mdc = (repo / ".cursor/rules/orion.mdc").read_text()
    assert mdc.startswith("---\n") and "alwaysApply: true" in mdc.split("---")[1]
    assert rb.sync(repo)["changed"] == []
    assert rb.check(repo) == []


def test_hand_written_notes_survive(repo):
    (repo / "CLAUDE.md").write_text("# My notes\n\nUse pnpm, not npm.\n")      # hand-written before the rulebook
    rb.sync(repo)
    text = (repo / "CLAUDE.md").read_text()
    keep = text[text.index(rb.KEEP_START):text.index(rb.KEEP_END)]
    assert "Use pnpm, not npm." in keep
    # Edit the notes between the markers, change the rules, sync again: the notes stay, the rules update.
    (repo / "CLAUDE.md").write_text(text.replace("Use pnpm, not npm.", "Use pnpm, not npm.\nRun tests with -x."))
    rules = repo / ".orion/rules.md"
    rules.write_text(rules.read_text().replace("Never push without asking.", "Never push without asking first."))
    assert rb.check(repo) == sorted(t for t, _ in rb.TARGETS)
    rb.sync(repo)
    text = (repo / "CLAUDE.md").read_text()
    assert "Never push without asking first." in text and "Run tests with -x." in text
    assert text.count(rb.KEEP_START) == 1 and text.count(rb.GENERATED_MARK) == 1


def test_add_rule_updates_all_files(repo):
    res = rb.add_rule("Orion, add a rule: never force-push to main", "Approvals", repo_root=repo)
    assert res["added"] and res["rule"] == {"id": "never-force-push-main", "text": "Never force-push to main",
                                            "section": "Approvals"}
    src = (repo / ".orion/rules.md").read_text()
    # inserted after the section's last rule (including its continuation line), before the next section
    assert src.index("[never-force-push-main]") > src.index("including releases.") > 0
    assert src.index("[never-force-push-main]") < src.index("## Secrets")
    for target, _ in rb.TARGETS:
        assert "[never-force-push-main] Never force-push to main" in (repo / target).read_text()
    again = rb.add_rule("never force-push to main.", "Approvals", repo_root=repo)
    assert not again["added"] and again["rule"]["id"] == "never-force-push-main"
    other = rb.add_rule("Never force push main branch", repo_root=repo)
    assert other["rule"]["id"] == "never-force-push-main-2" and other["rule"]["section"] == "General"
    assert (repo / ".orion/rules.md").read_text().rstrip().endswith("[never-force-push-main-2] Never force push main branch")
    for bad in ("", "  ", "# heading", "x <!-- y -->", "a" * 500):
        with pytest.raises(ValueError):
            rb.add_rule(bad, repo_root=repo)


def test_duplicate_ids_are_refused(repo):
    p = repo / ".orion/rules.md"
    p.write_text(p.read_text() + "- [no-push] Twice.\n")
    with pytest.raises(ValueError, match="no-push"):
        rb.sync(repo)


def test_violations_count_and_flag_once(repo, tmp_path, monkeypatch):
    import alerts
    sent = []
    monkeypatch.setattr(alerts, "emit", lambda *a, **k: sent.append(a))
    data = tmp_path / "data"
    token = "gh" + "p_" + "Q7f3Kp9Zr2Lm8Xv4Bn6Wt1Yc5Hd0JsGa3Uea"
    r1 = rb.record_violation("no-push", "codex", f"ran git push with {token}", repo_root=repo, data_dir=data)
    assert r1["count"] == 1 and not r1["flagged"] and r1["threshold"] == 3
    rb.record_violation("no-push", "claude-code", "pushed", repo_root=repo, data_dir=data)
    r3 = rb.record_violation("no-push", "codex", "pushed again", repo_root=repo, data_dir=data)
    assert r3["flagged"] and r3["newly_flagged"] and r3["agents"] == {"codex": 2, "claude-code": 1}
    r4 = rb.record_violation("no-push", "codex", "and again", repo_root=repo, data_dir=data)
    assert r4["flagged"] and not r4["newly_flagged"] and r4["count"] == 4
    assert len(sent) == 1 and "keeps breaking" in sent[0][1] and "codex" in sent[0][2]
    stored = (data / "rulebook_violations.json").read_text()
    assert token not in stored and "[redacted]" in stored
    listed = {r["id"]: r for r in rb.list_rules(repo, data)}
    assert listed["no-push"]["violations"] == 4 and listed["no-push"]["flagged"]
    assert "keeps being broken" in rb.describe(list(listed.values()))
    with pytest.raises(KeyError):
        rb.record_violation("made-up", "codex", "x", repo_root=repo, data_dir=data)
    assert rb.clear_violations("no-push", data) and rb.violations(data) == {}


def test_cli(repo):
    run = lambda *a: subprocess.run([sys.executable, "-I", str(DAEMON_DIR / "rulebook.py"), *a, "--repo", str(repo)],
                                    capture_output=True, text=True)
    assert run("check").returncode == 1
    assert "Rewrote" in run("sync").stdout
    assert run("check").returncode == 0
    out = run("add", "Never commit build output", "--section", "Secrets")
    assert out.returncode == 0 and "Added [never-commit-build-output]" in out.stdout
    assert "[never-commit-build-output]" in (repo / "AGENTS.md").read_text()
    assert "Approvals:" in run("list").stdout


def test_this_repo_generated_files_are_in_sync():
    """CLAUDE.md, AGENTS.md, GEMINI.md and Cursor's rules in the repo match .orion/rules.md."""
    if not (REPO_ROOT / ".orion" / "rules.md").exists():
        pytest.skip("no rulebook in this checkout")
    assert rb.check(REPO_ROOT) == [], "run: node scripts/rulebook/sync.mjs"
    ids = [r.id for r in rb.read_rules(REPO_ROOT)[1]]
    assert {"no-push", "no-secrets", "no-owner-name", "approval-rules", "plain-words"} <= set(ids)


@pytest.mark.skipif(not NODE, reason="node not installed")
def test_node_cli_check_on_this_repo():
    r = subprocess.run([NODE, str(REPO_ROOT / "scripts/rulebook/sync.mjs"), "check"], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr


# =====================================================================================  routes
class FakeBoard:
    def __init__(self):
        self.executors, self.proposed = {}, []

        class Store:
            def x(self, *a):
                pass
        self.store = Store()

    async def propose(self, task, summary, details, **kw):
        self.proposed.append((task, summary, details, kw))
        return {"id": "dec-rule", "summary": summary, "status": "pending"}


class FakeDaemon:
    def __init__(self):
        self.board = FakeBoard()


def test_routes(repo, tmp_path, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    cfg = tmp_path / "mcp.json"
    cfg.write_text(json.dumps({"roots": [str(repo)]}))
    monkeypatch.setenv("ORION_MCP_CONFIG", str(cfg))
    d = FakeDaemon()
    app = FastAPI()
    app.include_router(rb.make_router(lambda: d))
    assert rb.TASK_KEY in d.board.executors
    c = TestClient(app)
    p = str(repo)
    r = c.get("/rulebook/rules", params={"project": p}).json()
    assert [x["id"] for x in r["rules"]] == ["no-push", "ask-first", "no-secrets"]
    r = c.post("/rulebook/rules", json={"project": p, "text": "add a rule: never push without asking first"})
    assert r.status_code == 200 and r.json()["added"] and (repo / "GEMINI.md").exists()
    assert c.post("/rulebook/rules", json={"project": p, "text": ""}).status_code == 400
    assert c.post("/rulebook/rules", json={"project": str(tmp_path / "elsewhere"), "text": "x y z"}).status_code == 400
    assert c.post("/rulebook/sync", json={"project": p}).json()["changed"] == []
    # An agent's proposal becomes a decision; once signed the executor adds the rule.
    r = c.post("/rulebook/propose", json={"project": p, "text": "Run the tests before saying done"}).json()
    assert r["decision"]["id"] == "dec-rule"
    task, summary, details, kw = d.board.proposed[0]
    assert task == rb.TASK_KEY and kw["simulated"] is False and "CLAUDE.md" in details["files"]
    assert "Run the tests" not in (repo / ".orion/rules.md").read_text()
    out = d.board.executors[rb.TASK_KEY]({"packet": {"details": details}})
    assert out["state"] == "done" and "[run-tests-before-saying] Run the tests before saying done" in (repo / "CLAUDE.md").read_text()
    # violations
    r = c.post("/rulebook/violations", json={"project": p, "rule_id": "no-push", "agent": "codex", "evidence": "x"})
    assert r.status_code == 200 and r.json()["count"] == 1
    assert c.post("/rulebook/violations", json={"project": p, "rule_id": "nope", "agent": "a"}).status_code == 404
    assert c.get("/rulebook/violations").json()["violations"]["no-push"]["count"] == 1
    assert c.delete("/rulebook/violations/no-push").json()["cleared"]


def test_boardroom_task_type_always_asks():
    import boardroom
    rb._register_task_type()
    assert boardroom.TASK_TYPES[rb.TASK_KEY].max_level == 1    # sensitive: never becomes automatic


# =====================================================================================  MCP tools
class FakeBackend(Backend):
    def __init__(self, up=True):
        self.base_url, self.timeout, self._local_runner, self._preload = "http://test", 1, None, None
        self.up, self.calls = up, []

    async def call(self, method, path, body=None):
        self.calls.append((method, path, body))
        if not self.up:
            raise DaemonUnavailable("down")
        return 200, {"decision": {"id": "dec-rule", "summary": "Add a rule", "status": "pending"}}


def test_mcp_tools(repo):
    be = FakeBackend()
    tools = OrionTools(cfg=McpConfig(roots=[repo]), backend=be, default_project=str(repo))
    risks = {t["name"]: t["_meta"]["orion/risk"] for t in tools.list_tools()}
    assert risks["rulebook_list"] == "read" and risks["rulebook_add_rule"] == "approval"
    assert risks["rulebook_sync"] == "run"
    text, err = asyncio.run(tools.call("rulebook_list", {}))
    assert not err and "[no-push] Never push without asking." in text
    text, err = asyncio.run(tools.call("rulebook_sync", {}))
    assert not err and "CLAUDE.md" in text
    text, err = asyncio.run(tools.call("rulebook_add_rule", {"text": "Never skip the tests"}))
    assert not err and "Waiting for your approval" in text and "dec-rule" in text
    assert be.calls[-1][1] == "/rulebook/propose" and be.calls[-1][2]["text"] == "Never skip the tests"
    assert "Never skip" not in (repo / ".orion/rules.md").read_text()      # nothing changes before approval
    tools.backend = FakeBackend(up=False)
    text, err = asyncio.run(tools.call("rulebook_add_rule", {"text": "Never skip the tests"}))
    assert err and "isn't running" in text
