"""Crash auto-healer: parsing, dedupe, worktree isolation, fail-then-pass, approval before touching the repo."""
import asyncio
import json
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import boardroom as br
import crash_healer as ch
import heal_routes
from crash_healer import CrashHealer, crash_fingerprint, parse_trace

# ----------------------------------------------------------------------------- sample traces
NODE_TRACE = """\
/home/me/game/server.js:12
    return player.name.toUpperCase();
                       ^

TypeError: Cannot read properties of undefined (reading 'toUpperCase')
    at greet (/home/me/game/server.js:12:24)
    at Object.<anonymous> (/home/me/game/server.js:20:1)
    at Module._compile (node:internal/modules/cjs/loader:1256:14)
    at node:internal/main/run_main_module:23:47

Node.js v20.11.0
"""

RUST_TRACE = """\
   Compiling demo v0.1.0 (/home/me/demo)
thread 'main' panicked at src/stats.rs:7:5:
attempt to divide by zero
stack backtrace:
   0: rust_begin_unwind
             at /rustc/82e1608dfa6e0b5569232559e3d385fea5a93112/library/std/src/panicking.rs:645:5
   1: core::panicking::panic_fmt
             at /rustc/82e1608dfa6e0b5569232559e3d385fea5a93112/library/core/src/panicking.rs:72:14
   2: demo::stats::mean
             at ./src/stats.rs:7:5
   3: demo::main
             at ./src/main.rs:4:20
   4: core::ops::function::FnOnce::call_once
             at /rustc/82e1608dfa6e0b5569232559e3d385fea5a93112/library/core/src/ops/function.rs:250:5
note: Some details are omitted, run with `RUST_BACKTRACE=full` for a verbose backtrace.
"""

RUST_OLD = "thread 'worker-2' panicked at 'index out of bounds: the len is 3 but the index is 5', src/lib.rs:41:9\n" \
           "note: run with `RUST_BACKTRACE=1` environment variable to display a backtrace\n"

CHAINED = """\
Traceback (most recent call last):
  File "/srv/app/db.py", line 5, in load
    return json.loads(raw)
json.decoder.JSONDecodeError: Expecting value: line 1 column 1 (char 0)

During handling of the above exception, another exception occurred:

Traceback (most recent call last):
  File "/srv/app/main.py", line 9, in <module>
    cfg = load()
  File "/srv/app/db.py", line 7, in load
    raise RuntimeError("config is broken")
RuntimeError: config is broken
"""


def test_parse_python_chained_takes_last():
    t = parse_trace("starting...\n" + CHAINED)
    assert t.language == "python" and t.error_type == "RuntimeError" and t.message == "config is broken"
    assert [(f.file, f.line, f.function) for f in t.frames] == [("/srv/app/db.py", 7, "load"),
                                                               ("/srv/app/main.py", 9, "<module>")]


def test_parse_node():
    t = parse_trace(NODE_TRACE)
    assert t.language == "node" and t.error_type == "TypeError"
    assert "reading 'toUpperCase'" in t.message
    assert (t.frames[0].file, t.frames[0].line, t.frames[0].function) == ("/home/me/game/server.js", 12, "greet")
    assert [f.function for f in t.user_frames()] == ["greet", "Object.<anonymous>"]  # node:internal left out


def test_parse_rust_panic_with_backtrace():
    t = parse_trace(RUST_TRACE)
    assert t.language == "rust" and t.error_type == "panic" and t.message == "attempt to divide by zero"
    assert t.frames[0].function == "demo::stats::mean" and t.frames[0].line == 7
    assert any(f.function == "demo::main" for f in t.frames)
    assert not any("rustc" in f.file for f in t.frames)


def test_parse_rust_old_format_without_backtrace():
    t = parse_trace(RUST_OLD)
    assert t.language == "rust" and t.message.startswith("index out of bounds")
    assert (t.frames[0].file, t.frames[0].line) == ("src/lib.rs", 41)


def test_no_trace_means_no_crash():
    assert parse_trace("npm ERR! missing script: start\nexit code 1") is None
    assert parse_trace("Error: something\nno frames here") is None


def test_fingerprint_ignores_line_moves_and_values():
    a = parse_trace(CHAINED)
    b = parse_trace(CHAINED.replace("line 7", "line 12").replace("config is broken", "other text"))
    c = parse_trace(CHAINED.replace("RuntimeError", "KeyError"))
    assert crash_fingerprint(a) == crash_fingerprint(b) != crash_fingerprint(c)


# ----------------------------------------------------------------------------- a real buggy project
def git(cwd, *args):
    return subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True, check=True).stdout


def make_repo(tmp_path, *, with_git=True):
    repo = tmp_path / "proj"
    (repo / "tests").mkdir(parents=True)
    (repo / "calc.py").write_text(
        'API_TOKEN = "sk-live-abcdefghijklmnopqrstuvwxyz0123"\n\n\n'
        "def average(xs):\n    total = sum(xs)\n    return total / len(xs)\n")
    (repo / "main.py").write_text("from calc import average\n\nprint(average([]))\n")
    (repo / "tests" / "test_calc.py").write_text(
        "from calc import average\n\n\ndef test_average():\n    assert average([2, 4]) == 3\n")
    if with_git:
        git(repo, "init", "-q", "-b", "main")
        git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "add", ".")
        git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "init")
    return repo


def real_trace(repo):
    r = subprocess.run([sys.executable, "-B", "main.py"], cwd=repo, capture_output=True, text=True)
    assert r.returncode != 0 and "ZeroDivisionError" in r.stderr
    return r.stderr


REPRO_FAILS = ("from calc import average\n\n\ndef test_empty_list():\n    assert average([]) == 0\n\n\n"
               "if __name__ == \"__main__\":\n    test_empty_list()\n")
REPRO_PASSES = "from calc import average\n\n\ndef test_one():\n    assert average([2]) == 2\n"
GOOD_FIX = {"edits": [{"path": "calc.py", "find": "    return total / len(xs)\n",
                       "replace": "    if not xs:\n        return 0\n    return total / len(xs)\n"}],
            "explanation": "average() divided by zero for an empty list; it now returns 0 for an empty list."}
BAD_FIX = {"edits": [{"path": "calc.py", "find": "total = sum(xs)", "replace": "total = sum(xs) + 0"}],
           "explanation": "no-op"}


class FakeLLM:
    """Scripted model: answers by which step is asking. Records every prompt."""

    def __init__(self, tests=None, fixes=None):
        self.tests = list(tests or [{"path": "tests/test_orion_repro.py", "content": REPRO_FAILS}])
        self.fixes = list(fixes or [GOOD_FIX])
        self.calls = []

    async def frontier_text(self, system, user, **kw):
        step = "diagnose" if system is ch.DIAGNOSE_SYSTEM else "test" if system is ch.TEST_SYSTEM else "fix"
        self.calls.append((step, user))
        if step == "diagnose":
            out = {"diagnosis": "average() divides by the list length, which is 0 for an empty list."}
        elif step == "test":
            out = self.tests.pop(0) if len(self.tests) > 1 else self.tests[0]
        else:
            out = self.fixes.pop(0) if len(self.fixes) > 1 else self.fixes[0]
        return "Here you go:\n" + json.dumps(out), "fake-model"

    def steps(self):
        return [s for s, _ in self.calls]


class H:
    def __init__(self, tmp_path, llm=None, opt_in=True, with_git=True):
        self.loop = asyncio.new_event_loop()
        self.repo = make_repo(tmp_path, with_git=with_git)
        self.board = br.Boardroom(tmp_path / "data")
        self.llm = llm or FakeLLM()
        self.healer = CrashHealer(self.board, tmp_path / "data", self.llm)
        self.healer.update_settings(use_sandbox=False, test_timeout_s=60)
        self.notes = []
        self.healer.notify = lambda t, m: self.notes.append((t, m))
        if opt_in:
            self.healer.update_settings(project={"path": str(self.repo), "enabled": True})
        self.trace = real_trace(self.repo)

    def run(self, coro):
        return self.loop.run_until_complete(coro)

    def crash(self, **kw):
        async def go():
            rec = self.healer.observe_text(self.trace, cwd=str(self.repo), command="python main.py", **kw)
            await self.healer.settle()
            return self.healer.get(rec["id"]) if rec else None
        return self.run(go())

    def repo_state(self):
        return (git(self.repo, "status", "--porcelain"), git(self.repo, "rev-parse", "HEAD"),
                git(self.repo, "branch", "--list"), (self.repo / "calc.py").read_text())

    def verdict(self, v):
        d = next(x for x in self.board.pending() if x["task_type"] == "heal.fix")
        return self.run(self.board.verdict(d["id"], v, br.packet_hash(d["packet"])))


def test_full_pipeline_proposes_then_applies_only_after_approval(tmp_path):
    h = H(tmp_path)
    before = h.repo_state()
    rec = h.crash()
    assert rec["state"] == "proposed", rec["note"]
    assert h.llm.steps() == ["diagnose", "test", "fix"]
    assert rec["language"] == "python" and rec["error_type"] == "ZeroDivisionError"
    assert Path(rec["project"]) == h.repo.resolve()

    # the fix lives in a separate worktree; the user's checkout is untouched
    wt = Path(rec["worktree"])
    assert wt.is_dir() and h.repo.resolve() not in wt.resolve().parents
    assert "if not xs" in (wt / "calc.py").read_text()
    assert h.repo_state() == before

    # a Queue card / boardroom decision with the diff, test, results and a plain explanation
    [d] = h.board.pending()
    det = d["packet"]["details"]
    assert d["task_type"] == "heal.fix" and d["packet"]["summary"].startswith("Fix: ZeroDivisionError")
    assert "+    if not xs:" in det["diff"] and "tests/test_orion_repro.py" in det["diff"]
    assert det["test"] == REPRO_FAILS and det["results"]["after_fix"] == "The new test passes."
    assert det["results"]["project_tests"].startswith("Passed:")
    assert "nothing is pushed" in det["approving_does"]
    assert any("fix" in t.lower() for t, _ in h.notes)  # phone alert

    # secrets in the source never reach the model
    assert not any("sk-live-abcdef" in prompt for _, prompt in h.llm.calls)
    assert any(ch.SECRET_LINE in prompt for _, prompt in h.llm.calls)

    # approve -> a new branch with one commit; HEAD, main and the files on disk stay as they were
    d = h.verdict("approve")
    assert d["status"] == "approved", d
    branch = d["executed"]["branch"]
    assert branch.startswith("orion/heal-")
    assert git(h.repo, "rev-parse", "--abbrev-ref", "HEAD").strip() == "main"
    assert git(h.repo, "rev-parse", "main") == before[1]
    assert git(h.repo, "status", "--porcelain") == before[0] == "" and (h.repo / "calc.py").read_text() == before[3]
    files = git(h.repo, "show", "--name-only", "--format=", branch).split()
    assert sorted(files) == ["calc.py", "tests/test_orion_repro.py"]
    assert "if not xs" in git(h.repo, "show", f"{branch}:calc.py")
    assert git(h.repo, "rev-parse", f"{branch}~1") == before[1]
    assert not wt.exists() and len(git(h.repo, "worktree", "list").splitlines()) == 1
    assert h.healer.get(rec["id"])["state"] == "applied"


def test_reject_discards_worktree_and_touches_nothing(tmp_path):
    h = H(tmp_path)
    before = h.repo_state()
    rec = h.crash()
    wt = Path(rec["worktree"])
    assert wt.is_dir()
    h.verdict("reject")
    assert not wt.exists()
    assert h.repo_state() == before
    assert len(git(h.repo, "worktree", "list").splitlines()) == 1
    rec = h.healer.get(rec["id"])
    assert rec["state"] == "rejected" and rec["worktree"] is None


def test_fix_is_sensitive_and_never_runs_on_its_own(tmp_path):
    h = H(tmp_path)
    tt = br.TASK_TYPES["heal.fix"]
    assert tt.sensitive and tt.max_level == 1
    assert h.board.learner.set_level("heal.fix", 3) == 1   # capped
    before = h.repo_state()
    rec = h.crash()
    d = h.board.get(rec["decision_id"])
    assert d["status"] == "pending" and d["executed"] is None
    assert h.repo_state() == before
    # calling the executor for a decision that isn't the proposed one is refused
    with pytest.raises(ValueError):
        h.healer._exec_fix({"packet": {"details": {"crash_id": "crash-nope"}}})


def test_test_must_fail_first(tmp_path):
    h = H(tmp_path, FakeLLM(tests=[{"path": "tests/test_orion_repro.py", "content": REPRO_PASSES}]))
    before = h.repo_state()
    rec = h.crash()
    assert rec["state"] == "gave_up" and "couldn't write a test" in rec["note"]
    assert h.llm.steps() == ["diagnose", "test", "test"]     # max_attempts=2, never asked for a fix
    assert h.board.pending() == [] and h.repo_state() == before
    assert rec["worktree"] is None and len(git(h.repo, "worktree", "list").splitlines()) == 1


def test_second_test_attempt_that_fails_is_kept(tmp_path):
    h = H(tmp_path, FakeLLM(tests=[{"path": "tests/test_orion_repro.py", "content": REPRO_PASSES},
                                   {"path": "tests/test_orion_repro.py", "content": REPRO_FAILS}]))
    rec = h.crash()
    assert rec["state"] == "proposed"
    assert h.llm.steps() == ["diagnose", "test", "test", "fix"]
    assert "didn't reproduce" in h.llm.calls[2][1]   # feedback told the model why


def test_fix_must_pass_tests_or_nothing_is_proposed(tmp_path):
    h = H(tmp_path, FakeLLM(fixes=[BAD_FIX]))
    before = h.repo_state()
    rec = h.crash()
    assert rec["state"] == "gave_up" and "none passed" in rec["note"]
    assert h.llm.steps() == ["diagnose", "test", "fix", "fix"]
    assert h.board.pending() == [] and h.repo_state() == before


def test_fix_may_not_edit_tests(tmp_path):
    cheat = {"edits": [{"path": "tests/test_orion_repro.py", "find": "== 0", "replace": "is not None"}],
             "explanation": "x"}
    h = H(tmp_path, FakeLLM(fixes=[cheat, GOOD_FIX]))
    rec = h.crash()
    assert rec["state"] == "proposed"
    assert "test file" in h.llm.calls[3][1]


def test_not_a_git_repo_is_diagnosis_only(tmp_path):
    h = H(tmp_path, with_git=False)
    rec = h.crash()
    assert rec["state"] == "diagnosis_only" and "isn't a git repo" in rec["note"]
    assert h.llm.steps() == ["diagnose"]
    assert h.board.pending() == []


def test_dedupe_and_not_opted_in(tmp_path):
    h = H(tmp_path, opt_in=False)
    rec = h.crash()
    assert rec["state"] == "seen" and h.llm.calls == []
    again = h.crash()
    assert again["id"] == rec["id"] and again["count"] == 2
    assert len(h.healer.snapshot()["crashes"]) == 1
    # Retry runs it anyway (an explicit request)
    h.run(_retry(h.healer, rec["id"]))
    assert h.healer.get(rec["id"])["state"] == "proposed"


async def _retry(healer, crash_id):
    healer.retry(crash_id)
    await healer.settle()


def test_rate_limit_for_new_crashes(tmp_path):
    h = H(tmp_path, opt_in=False)
    h.healer.update_settings(max_new_per_hour=1)
    assert h.healer.observe_text(CHAINED, command="a") is not None
    assert h.healer.observe_text(NODE_TRACE, command="b") is None
    assert h.healer.observe_text(CHAINED, command="a")["count"] == 2   # repeats still counted
    assert h.healer.dropped == 1


def test_daily_budget(tmp_path):
    h = H(tmp_path)
    h.healer.update_settings(daily_model_calls=1)
    rec = h.crash()
    assert rec["state"] == "error" and "budget" in rec["note"]
    assert h.llm.steps() == ["diagnose"] and h.board.pending() == []


def test_runner_hook_records_crash(tmp_path):
    from runner import ProcessRunner
    h = H(tmp_path, opt_in=False)

    async def pub(_):
        pass

    async def go():
        r = ProcessRunner(pub)
        r.on_finish.append(h.healer.observe_run)
        run = await r.start(f"{sys.executable} main.py", cwd=str(h.repo))
        while run.ended_at is None:
            await asyncio.sleep(0.05)
        ok = await r.start(f"{sys.executable} -c 'print(1)'", cwd=str(h.repo))
        while ok.ended_at is None:
            await asyncio.sleep(0.05)
    h.run(go())
    [rec] = h.healer.snapshot()["crashes"]
    assert rec["error_type"] == "ZeroDivisionError" and rec["source"] == "runner"
    assert rec["command"].endswith("main.py") and Path(rec["project"]) == h.repo.resolve()


def test_restart_cleans_up_interrupted_and_rejected(tmp_path):
    h = H(tmp_path)
    rec = h.crash()
    wt = Path(rec["worktree"])
    d = h.board.get(rec["decision_id"])
    h.board.store.x("UPDATE decisions SET status='rejected' WHERE id=?", (d["id"],))  # rejected while we were down
    h.run(h.healer.start())
    assert not wt.exists() and h.healer.get(rec["id"])["state"] == "rejected"


def test_http_routes(tmp_path):
    h = H(tmp_path, opt_in=False)
    app = FastAPI()
    app.include_router(heal_routes.make_router(lambda: h.healer))
    c = TestClient(app)
    r = c.post("/heal/test", json={"text": RUST_TRACE, "command": "cargo run"})
    assert r.status_code == 200 and r.json()["crash"]["language"] == "rust"
    assert c.post("/heal/test", json={"text": "all good"}).status_code == 422
    body = c.get("/heal").json()
    assert body["crashes"][0]["message"] == "attempt to divide by zero" and body["settings"]["enabled"]
    assert c.post("/heal/crash-nope/retry").status_code == 404
    s = c.post("/heal/settings", json={"max_attempts": 9, "project": {"path": str(h.repo), "enabled": True}})
    assert s.json()["settings"]["max_attempts"] == 5
    assert s.json()["settings"]["projects"][str(h.repo.resolve())]["enabled"] is True


def test_wow_watcher_hands_crash_to_healer(tmp_path, monkeypatch):
    import os
    from tests.test_wow_agent import H as WowH
    monkeypatch.setenv("PATH", os.environ["PATH"])   # restored afterwards (the fake `wow` edits PATH)
    w = WowH(tmp_path)
    seen = []
    w.agent.on_crash.append(seen.append)
    (w.state / "wow-wanted").write_text("1")
    assert w.run(w.agent.check_once()) == "proposed"
    assert seen and "Last error: boom" in seen[0]
