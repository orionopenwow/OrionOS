"""OpenWoW agent: update/build only after a signed approval; the watcher proposes a restart when the server dies."""
import asyncio
import os
import stat

import pytest

import boardroom as br
from wow_agent import WowAgent


def fake_wow(tmp_path, active: str = "failed"):
    """A stand-in `wow` that records its calls, plus a stand-in systemctl."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    log = tmp_path / "calls.log"
    (bindir / "wow").write_text(f'#!/bin/sh\necho "$@" >> {log}\n'
                                'if [ "$1" = status ]; then echo "OpenWoW: crashed"; echo "Last error: boom"; fi\n')
    (bindir / "systemctl").write_text(f'#!/bin/sh\necho {active}\n[ {active} = active ]\n')
    for f in ("wow", "systemctl"):
        (bindir / f).chmod(0o755)
    return bindir, log


class H:
    def __init__(self, tmp_path, active="failed"):
        self.loop = asyncio.new_event_loop()
        bindir, self.log = fake_wow(tmp_path, active)
        os.environ["PATH"] = f"{bindir}:{os.environ['PATH']}"
        self.board = br.Boardroom(tmp_path / "data")
        self.state = tmp_path / "state"
        self.state.mkdir()
        self.agent = WowAgent(self.board, wow_bin=str(bindir / "wow"), state_dir=self.state, enabled=True)
        self.notes = []
        self.agent.notify = lambda t, m: self.notes.append((t, m))

    def run(self, c):
        return self.loop.run_until_complete(c)

    def settle(self):
        while self.agent._jobs:
            self.run(asyncio.gather(*list(self.agent._jobs)))

    def calls(self):
        return self.log.read_text().split("\n")[:-1] if self.log.exists() else []

    def verdict(self, task_type, v="approve"):
        d = next(x for x in self.board.pending() if x["task_type"] == task_type)
        return self.run(self.board.verdict(d["id"], v, br.packet_hash(d["packet"])))


@pytest.fixture(autouse=True)
def keep_path():
    old = os.environ["PATH"]
    yield
    os.environ["PATH"] = old


def test_update_waits_for_signature(tmp_path):
    h = H(tmp_path)
    out = h.run(h.agent.request("update"))
    assert out["already_waiting"] is False
    assert h.run(h.agent.request("update"))["already_waiting"] is True  # not asked twice
    d = h.board.pending()[0]
    assert d["task_type"] == "wow.update" and d["packet"]["simulated"] is False
    h.settle()
    assert h.calls() == []
    h.verdict("wow.update")
    h.settle()
    assert h.calls() == ["_do-update"]
    assert any(t == "OpenWoW updated" for t, _ in h.notes)


def test_rejected_build_runs_nothing(tmp_path):
    h = H(tmp_path)
    h.run(h.agent.request("build"))
    h.verdict("wow.build", "reject")
    h.settle()
    assert h.calls() == []


def test_unknown_action_refused(tmp_path):
    h = H(tmp_path)
    with pytest.raises(ValueError):
        h.run(h.agent.request("rm -rf"))


def test_update_and_build_always_ask(tmp_path):
    h = H(tmp_path)
    for tt in ("wow.update", "wow.build"):
        h.board.learner.set_level(tt, 3)
        assert h.board.learner.level(tt) == 1
    assert br.TASK_TYPES["wow.restart"].max_level == 3


def test_watcher_only_when_you_started_it(tmp_path):
    h = H(tmp_path)
    assert h.run(h.agent.check_once()) is None  # never started with `wow start`: leave it alone
    (h.state / "wow-wanted").touch()
    assert h.run(h.agent.check_once()) == "proposed"
    d = h.board.pending()[0]
    assert d["task_type"] == "wow.restart" and d["packet"]["details"]["last_error"] == "boom"
    assert h.run(h.agent.check_once()) is None  # one question at a time
    h.verdict("wow.restart")
    h.settle()
    assert h.calls()[-1] == "restart"


def test_watcher_quiet_while_running(tmp_path):
    h = H(tmp_path, active="active")
    (h.state / "wow-wanted").touch()
    assert h.run(h.agent.check_once()) is None
    assert h.board.pending() == []
