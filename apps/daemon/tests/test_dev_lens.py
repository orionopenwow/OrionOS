"""Toolkit: AI Blame (4), Context Pack (5), Perf Sentinel (13) and Log Lens (14), with temp git repos,
fake agent sessions, fake hyperfine/criterion output and fake journal JSON."""
import asyncio
import json
import os
import subprocess
import time
import types
from datetime import datetime, timedelta
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI

import ai_blame
import context_pack
import dev_lens
import log_lens
import perf_sentinel
from agent_watch import Source

GIT_ENV = {"GIT_AUTHOR_NAME": "Test", "GIT_AUTHOR_EMAIL": "t@example.com",
           "GIT_COMMITTER_NAME": "Test", "GIT_COMMITTER_EMAIL": "t@example.com"}
FAKE_KEY = "sk-" + "proj-" + "Zq8" + "Lw2Xv9Rt4Yb7Nm3Kp6Hd1Jf5Gs0Qa"   # built at runtime, never a real key


@pytest.fixture(autouse=True)
def git_identity(monkeypatch):
    for k, v in GIT_ENV.items():
        monkeypatch.setenv(k, v)


def sh(repo, *args, env=None):
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True,
                          env={**os.environ, **(env or {})}).stdout


def make_repo(path: Path, files=None, when=None) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", "-b", "main", str(path)], check=True)
    for rel, text in (files or {"README.md": "hello\n"}).items():
        f = path / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(text)
    sh(path, "add", "-A")
    stamp = {"GIT_COMMITTER_DATE": f"@{int(when)} +0000", "GIT_AUTHOR_DATE": f"@{int(when)} +0000"} if when else None
    sh(path, "commit", "-q", "-m", "first", env=stamp)
    return path


def commit(repo, rel, text, msg, when=None):
    (repo / rel).parent.mkdir(parents=True, exist_ok=True)
    (repo / rel).write_text(text)
    sh(repo, "add", "-A")
    env = {}
    if when is not None:
        stamp = f"@{int(when)} +0000"
        env = {"GIT_COMMITTER_DATE": stamp, "GIT_AUTHOR_DATE": stamp}
    sh(repo, "commit", "-q", "-m", msg, env=env)
    return sh(repo, "rev-parse", "HEAD").strip()


def iso(ts):
    return datetime.fromtimestamp(ts).astimezone().isoformat()


def claude_session(path: Path, prompt: str, start: float) -> Path:
    rows = [
        {"type": "user", "timestamp": iso(start), "cwd": "/x", "isMeta": True,
         "message": {"role": "user", "content": "<command-name>/clear</command-name>"}},
        {"type": "user", "timestamp": iso(start + 1), "message": {"role": "user", "content": prompt}},
        # one message streamed as two rows: its usage must count once
        {"type": "assistant", "timestamp": iso(start + 2),
         "message": {"id": "m1", "model": "claude-sonnet-4-5", "content": [{"type": "text", "text": "ok"}],
                     "usage": {"input_tokens": 100_000, "output_tokens": 20_000}}},
        {"type": "assistant", "timestamp": iso(start + 2),
         "message": {"id": "m1", "model": "claude-sonnet-4-5", "content": [{"type": "tool_use", "name": "Edit",
                                                                           "input": {"file_path": "/x/calc.py"}}],
                     "usage": {"input_tokens": 100_000, "output_tokens": 20_000}}},
        {"type": "user", "timestamp": iso(start + 3),
         "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t", "content": "fine"}]}},
        {"type": "assistant", "timestamp": iso(start + 4000),   # after the commit: not counted for it
         "message": {"id": "m2", "model": "claude-sonnet-4-5", "content": [],
                     "usage": {"input_tokens": 900_000, "output_tokens": 0}}},
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    return path


# =====================================================================================  AI Blame
def test_session_facts_reads_request_and_cost_but_hides_secrets(tmp_path):
    start = time.time() - 600
    p = claude_session(tmp_path / "s.jsonl", f"Fix the loot crash, my key is {FAKE_KEY} thanks", start)
    f = ai_blame.session_facts("Claude", p, until=start + 100)
    assert f["request"].startswith("Fix the loot crash") and FAKE_KEY not in f["request"]
    assert f["tokens_in"] == 100_000 and f["tokens_out"] == 20_000 and f["model"] == "claude-sonnet-4-5"
    assert f["cost"] == pytest.approx((100_000 * 3 + 20_000 * 15) / 1e6)
    assert ai_blame.session_facts("Claude", p)["tokens_in"] == 1_000_000      # whole session

    codex = tmp_path / "rollout-1.jsonl"
    rows = [{"type": "session_meta", "timestamp": iso(start), "payload": {"cwd": "/x"}},
            {"type": "turn_context", "timestamp": iso(start), "payload": {"model": "gpt-5"}},
            {"type": "response_item", "timestamp": iso(start + 1), "payload": {
                "type": "message", "role": "user", "content": [{"type": "input_text", "text": "<environment_context>"}]}},
            {"type": "response_item", "timestamp": iso(start + 2), "payload": {
                "type": "message", "role": "user", "content": [{"type": "input_text", "text": "Speed up the tick loop"}]}},
            {"type": "event_msg", "timestamp": iso(start + 3), "payload": {
                "type": "token_count", "info": {"total_token_usage": {"input_tokens": 50_000, "cached_input_tokens": 0,
                                                                      "output_tokens": 1000}}}}]
    codex.write_text("\n".join(json.dumps(r) for r in rows))
    f = ai_blame.session_facts("Codex", codex)
    assert f["request"] == "Speed up the tick loop" and f["model"] == "gpt-5" and f["cost"] > 0

    os.environ["ORION_BLAME_PROMPTS"] = "0"
    try:
        assert ai_blame.session_facts("Codex", codex)["request"] == ""
    finally:
        del os.environ["ORION_BLAME_PROMPTS"]


def test_agent_commit_gets_a_local_note_and_a_plain_answer(tmp_path):
    repo = make_repo(tmp_path / "proj", {"calc.py": "def add(a, b):\n    return a + b\n"}, when=time.time() - 7200)
    home = tmp_path / "home"
    start = time.time() - 300
    claude_session(home / ".claude" / "projects" / "proj" / "sess1.jsonl", "Make add() handle None", start)
    sources = [Source("Claude", home / ".claude" / "projects", (".jsonl",))]
    proofs = []
    idx = ai_blame.BlameIndex(tmp_path / "data", sources=sources, proofs=lambda: proofs)
    agent = {"id": "claude:sess1", "tool": "Claude", "cwd": str(repo), "started": start,
             "last_activity": time.time(), "files": ["calc.py"], "events": [{"text": "Reading calc.py"}]}
    idx.observe([agent])

    sha = commit(repo, "calc.py", "def add(a, b):\n    return (a or 0) + (b or 0)\n", "handle None in add")
    recs = idx.scan(repo)
    assert [r["sha"] for r in recs] == [sha]
    rec = recs[0]
    assert rec["by"] == "agent" and rec["tool"] == "Claude" and rec["session_id"] == "claude:sess1"
    assert rec["request"] == "Make add() handle None" and rec["cost"] == pytest.approx(0.6)
    note = sh(repo, "notes", "--ref=orion-blame", "show", sha)
    assert note.startswith("Orion AI Blame: Claude") and '"session_id": "claude:sess1"' in note
    assert sh(repo, "for-each-ref", "refs/notes/").strip().endswith("refs/notes/orion-blame")

    # Proof Gate finishes later: the note is updated with the verdict
    proofs.append({"id": "p1", "sha": sha[:12], "agent": "claude:sess1", "verdict": "green", "reason": "Proven",
                   "checks": [{"name": "test", "passed": 12, "failed": 0}], "finished_at": time.time()})
    assert idx.refresh_proofs(repo, rec["repo"]) == 1

    ans = ai_blame.why(idx, str(repo / "calc.py"), 2)
    assert ans["by"] == "agent" and ans["sha"] == sha
    a = ans["answer"]
    assert a.startswith("Claude, today at") and "handle None in add" in a and "Make add() handle None" in a
    assert "$0.60" in a and "Tests passed (Proof Gate: 12 tests pass)" in a
    assert ai_blame.why(idx, "calc.py", 2, repo=str(repo))["answer"].startswith("Claude")

    # the note alone is enough (e.g. a fresh index, or the MCP tool with Orion stopped)
    fresh = ai_blame.BlameIndex(tmp_path / "data2")
    assert "Make add() handle None" in ai_blame.why(fresh, str(repo / "calc.py"), 2)["answer"]

    # a commit long after the agent went quiet is yours
    later = time.time() + 7200
    sha2 = commit(repo, "notes.txt", "mine\n", "my own change", when=later)
    idx.scan(repo)
    hand = ai_blame.why(idx, str(repo / "notes.txt"), 1)
    assert hand["by"] == "you" and hand["answer"].startswith("You, by hand") and sha2 == hand["sha"]
    with pytest.raises(subprocess.CalledProcessError):
        sh(repo, "notes", "--ref=orion-blame", "show", sha2)        # no note for hand commits

    # not committed yet: the agent editing it is named
    (repo / "calc.py").write_text("x = 1\n")
    busy = ai_blame.why(idx, str(repo / "calc.py"), 1, agents=[agent])
    assert not busy["committed"] and "Claude is changing calc.py" in busy["answer"]


def test_commits_before_orion_watched_are_not_guessed(tmp_path):
    repo = make_repo(tmp_path / "old")
    commit(repo, "a.py", "x = 1\n", "ancient", when=time.time() - 5 * 86400)
    idx = ai_blame.BlameIndex(tmp_path / "data")
    ans = ai_blame.why(idx, str(repo / "a.py"), 1)
    assert ans["by"] == "unknown" and "wasn't watching" in ans["answer"]
    with pytest.raises(ValueError):
        ai_blame.why(idx, str(repo / "a.py"), 0)
    with pytest.raises(ValueError):
        ai_blame.why(idx, str(repo / "missing.py"), 1)
    assert ai_blame.find_file([repo], "a py")[1] == "a.py"
    assert ai_blame.find_file([repo], "nothing here") is None


# =====================================================================================  Context Pack
PACK_REPO = {
    "src/vault_sync.py": "class VaultSync:\n    def sync_vault(self):\n        raise KeyError('vault index')\n",
    "src/vault_store.ts": "export function loadVault() {}\nexport const saveVault = (x) => x;\n",
    "src/engine.rs": "pub struct Engine;\nimpl Engine {\n    pub fn tick(&self) {}\n}\n",
    "src/helpers.py": "def tidy():\n    pass\n",
    "src/other.py": "def unrelated():\n    pass\n",
    "docs/PLAN.md": "# Plan\n## Vault sync decision\nWe decided vault sync runs every 10 minutes and never deletes.\n"
                    "## Audio\nNothing about the topic.\n",
    ".orion/rules.md": "- [no-push] Never push without asking.\n",
    ".orion/batons/1-claude-to-codex.md": "# Hand-off\nThe vault sync bug: KeyError on empty index.\n",
}


def test_context_pack_builds_a_small_brief_and_learns(tmp_path):
    repo = make_repo(tmp_path / "proj", PACK_REPO)
    healer = types.SimpleNamespace(snapshot=lambda n: {"crashes": [
        {"project": str(repo), "error_type": "KeyError", "message": "'vault index'", "count": 3, "state": "seen",
         "frames": [{"file": "src/vault_sync.py", "line": 3}]},
        {"project": str(tmp_path / "elsewhere"), "error_type": "OtherError", "message": "not this repo"}]})
    runner = types.SimpleNamespace(list=lambda: [
        {"cwd": str(repo), "command": "pytest", "running": False, "exit_code": 1,
         "tail": ["FAILED test_vault", f"token={FAKE_KEY}"]}])
    saved = []
    meter = types.SimpleNamespace(record_saving=lambda src, n: saved.append((src, n)))
    packer = context_pack.ContextPacker(tmp_path / "data", healer=healer, runner=runner, meter=meter,
                                       vault_dir=tmp_path / "no-vault")
    pack = packer.build("Fix the vault sync bug", repo, budget=2000)
    md = pack["markdown"]
    assert pack["files"][0] == "src/vault_sync.py" and "src/vault_store.ts" in pack["files"]
    assert "src/other.py" not in pack["files"]
    assert "Never push without asking" in md and "VaultSync, sync_vault" in md
    assert "KeyError" in md and "OtherError" not in md and "FAILED test_vault" in md
    assert "Vault sync decision" in md and "every 10 minutes" in md and "Hand-off note" in md
    assert FAKE_KEY not in md
    assert pack["tokens"] <= 2000 + 80 and pack["whole_repo_tokens"] > 0
    assert saved and saved[0][0] == "context_pack" and saved[0][1] == pack["saved_tokens"]
    assert packer.stats()["packs"] == 1

    # an agent working on that task opened helpers.py: next time it is suggested for vault work
    assert "src/helpers.py" not in pack["files"]
    agent = {"id": "claude:s", "cwd": str(repo), "files": [], "events": [{"text": "Reading helpers.py"}]}
    assert packer.observe([agent]) == 1 and packer.observe([agent]) == 0   # learned once per session
    again = packer.build("vault sync crash", repo)
    assert "src/helpers.py" in again["files"]
    with pytest.raises(ValueError):
        packer.build("", repo)


def test_symbols_and_words():
    assert context_pack.words("fixVaultSync for the loot_drops bugs") == ["vault", "sync", "loot", "drop"]
    assert context_pack.symbols_in(Path("a.rs"), "pub fn tick() {}\nstruct World;\nimpl Server for World {}\n") == \
        ["tick", "World"]
    assert context_pack.symbols_in(Path("a.ts"), "export default function main() {}\ninterface Props {}\n") == \
        ["main", "Props"]
    assert context_pack.symbols_in(Path("a.go"), "func (s *S) Run() {}\ntype S struct{}\n") == ["Run", "S"]
    assert context_pack.symbols_in(Path("a.bin"), "x") == []


def test_compute_meter_keeps_token_savings(tmp_path):
    import compute_meter as cm
    m = cm.ComputeMeter(tmp_path / "meter.json", tmp_path / "none.json")
    m.record_saving("context_pack", 1200)
    m.record_saving("context_pack", 300)
    assert m.snapshot()["saved"] == {"today": 1500, "by_tool": {"context_pack": 1500}}
    assert cm.ComputeMeter(tmp_path / "meter.json", tmp_path / "none.json").snapshot()["saved"]["today"] == 1500


# =====================================================================================  Perf Sentinel
class FakeBench:
    """Stands in for hyperfine / cargo: returns queued mean times, writing the JSON hyperfine would export."""

    def __init__(self, means):
        self.means = list(means)
        self.calls = []

    async def __call__(self, argv, cwd, timeout):
        self.calls.append(argv)
        if argv[0] == "hyperfine":
            out = Path(argv[argv.index("--export-json") + 1])
            mean = self.means.pop(0)
            out.write_text(json.dumps({"results": [{"command": argv[-1], "mean": mean, "stddev": 0.01,
                                                    "median": mean, "times": [mean]}]}))
            return 0, ""
        if argv[:2] == ["cargo", "bench"]:
            line = {"reason": "benchmark-complete", "id": "world/tick", "typical": {"estimate": 2500.0, "unit": "ns"}}
            return 0, "Compiling...\n" + json.dumps(line) + "\n"
        return 0, ""


def test_perf_sentinel_trends_and_slowdown_card(tmp_path, monkeypatch):
    repo = make_repo(tmp_path / "server", {".orion/perf.json": json.dumps({
        "benches": [{"name": "server start", "command": "./start --check", "runs": 3},
                    {"name": "build size", "size": "dist"}],
        "when": ["commit", "nightly"]}), "dist/app.js": "x" * 1000})
    monkeypatch.setattr(perf_sentinel.shutil, "which", lambda name: "/usr/bin/" + name)
    cards = []
    fake = FakeBench([1.00, 1.01, 0.99, 1.00, 1.30])
    ps = perf_sentinel.PerfSentinel(tmp_path / "data", exec_fn=fake, card=cards.append)
    for i in range(4):
        res = asyncio.run(ps.run(repo))
        assert {r["bench"] for r in res["results"]} == {"server start", "build size"} and not res["regressions"]
    good = sh(repo, "rev-parse", "HEAD").strip()
    commit(repo, "slow.py", "sleep\n", "add a slow start step")
    commit(repo, "slow2.py", "sleep more\n", "and another")
    res = asyncio.run(ps.run(repo))
    assert len(res["regressions"]) == 1 and len(cards) == 1
    reg = cards[0]
    assert reg["bench"] == "server start" and reg["pct"] == pytest.approx(30.0, abs=0.5)
    assert f"between {good[:7]} and" in reg["text"] and "(2 commits)" in reg["text"] and "1.00 s -> 1.30 s" in reg["text"]
    assert any("add a slow start step" in c for c in reg["commits"])
    assert ps.regressions()[0]["bench"] == "server start"
    hf = fake.calls[0]
    assert hf[0] == "hyperfine" and "--runs" in hf and hf[hf.index("--runs") + 1] == "3"

    assert ps.wants(repo, "commit") and not ps.wants(repo, "proof")
    assert asyncio.run(ps.on_proof({"verdict": "green", "repo_path": str(repo)})) is None
    with pytest.raises(ValueError):
        asyncio.run(ps.run(make_repo(tmp_path / "noperf")))


def test_perf_compare_last_week_and_parsers(tmp_path, monkeypatch):
    now = time.time()
    ps = perf_sentinel.PerfSentinel(tmp_path / "data", clock=lambda: now)
    for days, v in ((8, 0.0040), (7, 0.0042), (6, 0.0041), (0, 0.0031)):
        ps.add_result("/r/openwow", "openwow", "world tick", v, "s", "abc", ts=now - days * 86400)
    ans = ps.compare("server")
    assert ans.startswith("Yes.") and "faster than last week" in ans and "3.1 ms now" in ans
    assert "don't have a benchmark" in ps.compare("blender")

    assert perf_sentinel.parse_hyperfine('{"results": [{"mean": 0.5, "stddev": 0.1}]}')["value"] == 0.5
    assert perf_sentinel.parse_hyperfine("nope") is None
    est = tmp_path / "target" / "criterion" / "world" / "tick" / "new" / "estimates.json"
    est.parent.mkdir(parents=True)
    est.write_text(json.dumps({"mean": {"point_estimate": 1500.0}, "std_dev": {"point_estimate": 10.0}}))
    found = perf_sentinel.read_criterion_dir(tmp_path / "target", time.time() - 60)
    assert found["world/tick"]["value"] == pytest.approx(1.5e-6)

    repo = make_repo(tmp_path / "rs", {".orion/perf.json": json.dumps({"benches": [{"name": "crit", "cargo_bench": True}]})})
    res = asyncio.run(perf_sentinel.PerfSentinel(tmp_path / "d3", exec_fn=FakeBench([])).run(repo))
    assert res["results"][0]["bench"] == "crit: world/tick" and res["results"][0]["shown"] == "2.5 µs"
    assert perf_sentinel.load_config(repo)["when"] == ["nightly"]


def test_perf_hook_never_replaces_someone_elses(tmp_path):
    repo = make_repo(tmp_path / "p")
    path = Path(perf_sentinel.install_hook(repo, 9120))
    assert perf_sentinel.HOOK_MARK in path.read_text() and os.access(path, os.X_OK)
    perf_sentinel.install_hook(repo, 9120)                       # ours: fine to rewrite
    path.write_text("#!/bin/sh\necho mine\n")
    with pytest.raises(ValueError):
        perf_sentinel.install_hook(repo, 9120)


# =====================================================================================  Log Lens
def jrow(ts, msg, unit="", ident="", prio=6, user=False):
    row = {"__REALTIME_TIMESTAMP": str(int(ts * 1e6)), "MESSAGE": msg, "PRIORITY": str(prio),
           "SYSLOG_IDENTIFIER": ident, "_HOSTNAME": "lab"}
    row["_SYSTEMD_USER_UNIT" if user else "_SYSTEMD_UNIT"] = unit
    return row


def fake_night():
    today = datetime.now().replace(hour=10, minute=0, second=0, microsecond=0)
    three = today.replace(hour=3).timestamp()
    system = [jrow(three - 3000, "Started Daily apt download activities.", "apt-daily.service", "systemd"),
              jrow(three - 120, "Out of memory: Killed process 4242 (worldserver) total-vm:9000kB", "", "kernel", 3),
              jrow(three - 100, "cron job ran fine", "cron.service", "CRON"),
              jrow(three - 90, f"login with password={FAKE_KEY}", "sshd.service", "sshd", 4),
              jrow(three - 80, [104, 105], "", "weird")]    # non-UTF-8 message sent as bytes
    user = [jrow(three - 119, "openwow.service: Main process exited, code=killed, status=9/KILL", "openwow.service",
                 "systemd", 4, True),
            jrow(three - 60, "openwow.service: Scheduled restart job, restart counter is at 1.", "openwow.service",
                 "systemd", 6, True),
            jrow(three - 59, "Started OpenWoW world server.", "openwow.service", "systemd", 6, True)]
    return today.timestamp(), three, system, user


def test_log_lens_explains_a_3am_restart_from_the_journal(tmp_path):
    now, three, system, user = fake_night()
    asked = []

    def jfn(is_user, since, until):
        asked.append((is_user, since, until))
        rows = user if is_user else system
        return [r for r in rows if since <= int(r["__REALTIME_TIMESTAMP"]) / 1e6 <= until]

    changes = [{"ts": three - 1800, "what": "Updated OpenWoW", "why": "you approved it", "source": "wow"},
               {"ts": three - 5 * 3600, "what": "too early", "source": "x"}]
    lens = log_lens.LogLens(tmp_path, journal_fn=jfn, summarise=None, changes_fn=lambda: changes,
                            clock=lambda: now)
    res = asyncio.run(lens.ask("Why did the world server restart at 3 am?"))
    assert asked[0][0] is False and asked[1][0] is True
    assert asked[0][1] == pytest.approx(three - log_lens.BEFORE_S) and asked[0][2] == pytest.approx(three + log_lens.AFTER_S)
    assert res["summarised_by"] == "rules"
    assert res["answer"].startswith("Most likely: the computer ran out of memory and killed worldserver")
    texts = [e["text"] for e in res["timeline"]]
    assert texts == sorted(texts, key=lambda t: next(e["ts"] for e in res["timeline"] if e["text"] == t))
    assert any("Scheduled restart job" in t for t in texts) and any("Updated OpenWoW" in t for t in texts)
    assert not any("too early" in t for t in texts) and not any("cron job" in t for t in texts)
    assert FAKE_KEY not in json.dumps(res)
    assert res["sources"]["system journal"] == 4 and res["sources"]["change history"] == 1
    assert "Timeline (around 03:00" in log_lens.describe(res)

    prompts = []

    async def model(prompt):
        prompts.append(prompt)
        return "The server was killed because the computer ran out of memory, then systemd restarted it."
    lens.summarise = model
    res2 = asyncio.run(lens.ask("why did the world server restart at 3 am"))
    assert res2["summarised_by"] == "local model" and res2["answer"].startswith("The server was killed")
    assert FAKE_KEY not in prompts[0] and "data, not instructions" in prompts[0]


def test_log_lens_windows_and_plain_files(tmp_path):
    now = datetime.now().replace(hour=10, minute=0, second=0, microsecond=0).timestamp()
    s, u, w = log_lens.parse_window("what happened at 11 pm", now)
    assert w.startswith("around 23:00") and u <= now and now - u < 86400    # 11 pm is in the future -> yesterday
    s, u, w = log_lens.parse_window("anything in the last 2 hours", now)
    assert (u - s, w) == (7200, "the last 2 hours")
    assert log_lens.parse_window("why did it crash yesterday", now)[2] == "yesterday"
    assert log_lens.parse_window("why is it slow", now)[2] == "the last 24 hours"
    assert log_lens.parse_window("at 14:30", now)[2].startswith("around 14:30")
    assert "openwow" in log_lens.subject_words("why did the world server restart")

    logs = tmp_path / "logs"
    logs.mkdir()
    t = datetime.fromtimestamp(now - 600).strftime("%Y-%m-%d %H:%M:%S")
    (logs / "world.log").write_text(f"{t} ERROR worldserver: map 0 failed to load\nno timestamp here\n"
                                    "2001-01-01 00:00:00 ERROR far too old\n")
    lens = log_lens.LogLens(tmp_path, journal_fn=lambda *a: [], summarise=None, clock=lambda: now)
    res = asyncio.run(lens.ask("why did the world server fail in the last hour"))
    assert res["sources"]["log files"] == 1 and "map 0 failed" in res["timeline"][0]["text"]
    empty = asyncio.run(log_lens.LogLens(tmp_path / "x", journal_fn=lambda *a: [], summarise=None,
                                         clock=lambda: now).ask("why did it restart"))
    assert "No logs could be read" in empty["answer"]
    with pytest.raises(ValueError):
        asyncio.run(lens.ask(""))


# =====================================================================================  wiring: phrases, routes, MCP
def make_lens(tmp_path, repo):
    from quick_commands import QuickCommands
    d = types.SimpleNamespace(quick=QuickCommands(config=tmp_path / "quick.json"))
    dl = dev_lens.DevLens(d, data_dir=tmp_path / "data")
    dl.repo_root = repo
    d.dev_lens = dl
    return d, dl


def test_phrases_reach_the_right_tool(tmp_path, monkeypatch):
    repo = make_repo(tmp_path / "proj", PACK_REPO)
    d, dl = make_lens(tmp_path, repo)
    q = d.quick
    for said, name in (("Orion, why did line 2 of vault_sync.py change?", "blame"),
                       ("who changed line 1 in helpers py", "blame"),
                       ("Orion, brief Claude on the vault bug", "brief"),
                       ("is the server faster than last week?", "perf"),
                       ("Why did the world server restart at 3 am?", "loglens"),
                       ("what happened last night", "loglens")):
        m = q.match(said)
        assert m is not None and m[1][0].startswith(f"toolkit:{name}:"), said

    plugin = {p.name: p for p in q.plugins}
    out = asyncio.run(plugin["blame"].run("why did line 2 of vault_sync py change"))
    assert "wasn't watching" in out or out.startswith("You, by hand")
    assert "can't find a file" in asyncio.run(plugin["blame"].run("why did line 2 of nope zz change"))
    copied = []
    monkeypatch.setattr(context_pack, "copy_to_clipboard", lambda text: copied.append(text) or True)
    out = asyncio.run(plugin["brief"].run("claude|the vault sync bug"))
    assert out.startswith("Brief for Claude") and "clipboard" in out and "vault_sync.py" in copied[0]
    assert "no measurements" in asyncio.run(plugin["perf"].run("7|server"))
    dl.logs.journal_fn = lambda *a: []
    dl.logs.summarise = None
    assert "No logs could be read" in asyncio.run(plugin["loglens"].run("why did the server restart at 3 00"))


def test_routes_and_mcp_tools(tmp_path, monkeypatch):
    repo = make_repo(tmp_path / "proj", PACK_REPO)
    d, dl = make_lens(tmp_path, repo)
    monkeypatch.setattr(dev_lens, "_allowed", lambda p: Path(p).resolve())
    app = FastAPI()
    app.include_router(dev_lens.make_router(lambda: d))
    dl.logs.journal_fn = lambda *a: []
    dl.logs.summarise = None

    async def go():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
            r1 = await c.get("/blame", params={"file": str(repo / "src/helpers.py"), "line": 1})
            r2 = await c.get("/blame", params={"file": str(repo / "src/helpers.py"), "line": 99})
            r3 = await c.post("/context/pack", json={"task": "vault sync", "repo": str(repo), "budget_tokens": 1500})
            r4 = await c.get("/logs/ask", params={"q": "why did orion restart today"})
            r5 = await c.get("/perf/compare", params={"q": "server"})
            r6 = await c.get("/blame/recent")
            return r1, r2, r3, r4, r5, r6
    r1, r2, r3, r4, r5, r6 = asyncio.run(go())
    assert r1.status_code == 200 and r1.json()["sha"] and r1.json()["answer"]
    assert r2.status_code == 400 and "error" in r2.json()
    assert r3.status_code == 200 and r3.json()["files"][0] == "src/vault_sync.py"
    assert r4.status_code == 200 and r4.json()["window"] == "today"
    assert r5.status_code == 200 and "answer" in r5.json()
    assert r6.status_code == 200 and "commits" in r6.json()

    from mcp_tools import DaemonUnavailable, McpConfig, OrionTools

    class Down:
        async def call(self, *a, **k):
            raise DaemonUnavailable("off")
    tools = OrionTools(cfg=McpConfig(roots=[tmp_path]), backend=Down(), default_project=str(repo))
    listed = {t["name"]: t for t in tools.list_tools()}
    assert {listed[n]["_meta"]["orion/risk"] for n in ("ai_blame", "context_pack", "log_lens")} == {"read"}
    text, err = asyncio.run(tools.call("context_pack", {"task": "fix the vault sync bug"}))
    assert not err and "# Brief: fix the vault sync bug" in text
    text, err = asyncio.run(tools.call("ai_blame", {"file": "src/helpers.py", "line": 1}))
    assert not err and text.startswith("src/helpers.py:1")
    text, err = asyncio.run(tools.call("ai_blame", {"file": "/etc/passwd", "line": 1}))
    assert err and "isn't inside the project" in text
