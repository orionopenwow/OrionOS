"""Setup agent: proposes first-run work, runs it only after a signed verdict, and reports progress."""
import asyncio
import subprocess

import pytest

import boardroom as br
from setup_agent import WORKSPACE, SetupAgent


class Harness:
    def __init__(self, tmp_path, recipe_result="ok"):
        self.loop = asyncio.new_event_loop()
        self.board = br.Boardroom(tmp_path / "data")
        self.agent = SetupAgent(self.board, tmp_path / "data", projects_dir=tmp_path / "projects", enabled=True)
        self.notes = []
        self.agent.notify = lambda t, m: self.notes.append((t, m))
        self.ran = []

        async def fake_recipe(name):
            self.ran.append(name)
            return recipe_result if not callable(recipe_result) else recipe_result(name)
        self.agent.run_recipe = fake_recipe

    def run(self, coro):
        return self.loop.run_until_complete(coro)

    def settle(self):
        self.run(asyncio.sleep(0.05))
        while self.agent._jobs:
            self.run(asyncio.gather(*list(self.agent._jobs)))

    def approve(self, task_type):
        d = next(x for x in self.board.pending() if x["task_type"] == task_type)
        return self.run(self.board.verdict(d["id"], "approve", br.packet_hash(d["packet"])))


def test_nothing_installs_until_signed(tmp_path):
    h = Harness(tmp_path)
    assert h.run(h.agent.check()) == ["workspace"]
    assert h.run(h.agent.check()) == []  # not proposed twice
    d = h.board.pending()[0]
    assert d["packet"]["simulated"] is False and d["packet"]["details"]["recipes"] == [n for n, _ in WORKSPACE]
    assert h.ran == []
    out = h.approve("setup.workspace")
    assert out["status"] == "approved" and out["executed"]["started"] is True
    h.settle()
    assert h.ran == [n for n, _ in WORKSPACE]
    assert h.agent.state()["workspace"] == "done"
    assert any("Workspace ready" in t for t, _ in h.notes)


def test_workspace_never_auto_runs(tmp_path):
    h = Harness(tmp_path)
    h.board.learner.set_level("setup.workspace", 3)  # capped: installing software always asks
    assert h.board.learner.level("setup.workspace") == 1
    h.run(h.agent.check())
    assert h.board.pending()[0]["status"] == "pending" and h.ran == []


def test_failed_install_is_offered_again(tmp_path):
    h = Harness(tmp_path, recipe_result=lambda n: "failed" if n == "discord" else "ok")
    h.run(h.agent.check())
    h.approve("setup.workspace")
    h.settle()
    st = h.agent.state()
    assert st["workspace"] == "failed" and st["results"]["discord"] == "failed"
    assert [d["task_type"] for d in h.board.pending()] == ["setup.workspace"]  # asked again


def test_rejected_workspace_runs_nothing(tmp_path):
    h = Harness(tmp_path)
    h.run(h.agent.check())
    d = h.board.pending()[0]
    h.run(h.board.verdict(d["id"], "reject", br.packet_hash(d["packet"])))
    h.settle()
    assert h.ran == []


def test_repo_address_is_validated(tmp_path):
    h = Harness(tmp_path)
    with pytest.raises(ValueError):
        h.agent.set_repo("rm -rf / ; echo")
    with pytest.raises(ValueError):
        h.agent.set_repo("https://github.com/you/openwow", name="../etc")
    assert h.agent.set_repo("https://github.com/you/openwow.git")["state"] == "new"


def test_clone_after_workspace_and_signature(tmp_path):
    # a real local git repo stands in for GitHub
    src = tmp_path / "remote"
    src.mkdir()
    subprocess.run(["git", "init", "-q", str(src)], check=True)
    (src / "README.md").write_text("OpenWoW\n")
    subprocess.run(["git", "-C", str(src), "add", "."], check=True)
    subprocess.run(["git", "-C", str(src), "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "init"], check=True)

    h = Harness(tmp_path)
    h.run(h.agent.check())
    h.approve("setup.workspace")
    h.settle()
    h.agent.set_repo(f"https://example.invalid/you/openwow")  # validated shape
    st = h.agent.state()
    st["repos"]["openwow"]["url"] = str(src)  # point at the local stand-in (bypasses the https check in the test only)
    h.agent._save(st)
    assert h.run(h.agent.check()) == ["clone:openwow"]
    d = next(x for x in h.board.pending() if x["task_type"] == "setup.clone")
    assert d["packet"]["details"]["repo"] == str(src)
    assert not (tmp_path / "projects" / "openwow").exists()
    h.approve("setup.clone")
    h.settle()
    assert (tmp_path / "projects" / "openwow" / "README.md").read_text() == "OpenWoW\n"
    assert h.agent.state()["repos"]["openwow"]["state"] == "done"


def test_clone_refused_if_repo_changed_after_proposal(tmp_path):
    h = Harness(tmp_path)
    st = h.agent.state()
    st["workspace"] = "done"
    h.agent._save(st)
    h.agent.set_repo("https://github.com/you/openwow")
    h.run(h.agent.check())
    h.agent.set_repo("https://github.com/someone-else/evil")
    d = next(x for x in h.board.pending() if x["task_type"] == "setup.clone")
    out = h.run(h.board.verdict(d["id"], "approve", br.packet_hash(d["packet"])))
    assert out["status"] == "failed"
    h.settle()
    assert not (tmp_path / "projects" / "openwow").exists()


def test_run_recipe_only_accepts_known_names(tmp_path):
    board = br.Boardroom(tmp_path / "d")
    agent = SetupAgent(board, tmp_path / "d", enabled=True)
    assert asyncio.new_event_loop().run_until_complete(agent.run_recipe("rm-rf")) == "failed"
