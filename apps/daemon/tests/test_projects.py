"""Start a project with Claude (projects.py): temp folders and a fake missions module, no real agents."""
from __future__ import annotations

import json
import subprocess
import types
import urllib.error
import urllib.request
from pathlib import Path

import pytest

import projects
from projects import ProjectError, Projects, clean_name


class FakeMC:
    def __init__(self):
        self.missions: dict[str, dict] = {}

    def get(self, mid):
        return self.missions.get(mid)

    @staticmethod
    def public(m):
        return dict(m)


def fake_missions(fail: str | None = None):
    mc = FakeMC()
    calls: list[dict] = []

    def create_mission(title, repo=None, agent=None, budget_usd=None, race=1, source="hud", **kw):
        if fail:
            raise RuntimeError(fail)
        mid = f"m-{len(calls) + 1:04d}"
        rec = {"id": mid, "title": title, "repo": repo, "agent": agent or "claude", "agent_label": "Claude Code",
               "state": "queued", "budget_usd": budget_usd, "source": source, "worktree": None, "merge": None,
               "description": kw.get("description", "")}
        calls.append(rec)
        mc.missions[mid] = rec
        return rec

    mod = types.SimpleNamespace(create_mission=create_mission, current=lambda: mc)
    return mod, calls, mc


@pytest.fixture()
def env(tmp_path):
    mod, calls, mc = fake_missions()
    opened: list[str] = []
    browsed: list[str] = []
    pj = Projects(tmp_path / "data", root=tmp_path / "Orion Projects", missions_mod=mod,
                  opener=opened.append, browser=browsed.append)
    yield types.SimpleNamespace(pj=pj, calls=calls, mc=mc, opened=opened, browsed=browsed, tmp=tmp_path)
    pj.stop()


def git(path: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=path, capture_output=True, text=True, check=True).stdout


def test_names_are_safe_on_windows_and_linux():
    assert clean_name("  My  Game!! ") == "My Game"
    assert clean_name("../../etc/passwd") == "etcpasswd"
    assert clean_name("a/b\\c") == "abc"
    with pytest.raises(ProjectError):
        clean_name("///")
    with pytest.raises(ProjectError):
        clean_name("CON")
    with pytest.raises(ProjectError):
        clean_name("lpt1.txt")
    assert len(clean_name("x" * 200)) == 60


def test_default_root_is_orion_projects_in_home(monkeypatch):
    monkeypatch.delenv("ORION_PROJECTS_DIR", raising=False)
    assert projects.projects_root() == Path.home() / "Orion Projects"


def test_web_game_project_is_made_with_rules_git_and_a_mission(env):
    out = env.pj.create("Star Catcher", "web-game", "Catch falling stars with a little ship", budget_usd=2.0)
    path = env.tmp / "Orion Projects" / "Star Catcher"
    assert path.is_dir()
    for f in ("index.html", "game.js", "game.test.js", "CLAUDE.md", "README.md", ".gitignore"):
        assert (path / f).is_file(), f
    assert not (path / "gitignore").exists()
    assert "Star Catcher" in (path / "index.html").read_text()
    rules = (path / "CLAUDE.md").read_text()
    for words in ("Small steps", "Tests", "Plain words", "No secrets", "Never push"):
        assert words in rules
    assert "Catch falling stars" in rules and "Catch falling stars" in (path / "README.md").read_text()
    # git: one commit on main, nothing left uncommitted
    assert git(path, "rev-parse", "--abbrev-ref", "HEAD").strip() == "main"
    assert len(git(path, "log", "--oneline").splitlines()) == 1
    assert git(path, "status", "--porcelain").strip() == ""
    # mission started on that folder, from the HUD
    assert len(env.calls) == 1
    m = env.calls[0]
    assert m["repo"] == str(path) and m["source"] == "hud" and m["budget_usd"] == 2.0
    assert "CLAUDE.md" in m["description"] and "Catch falling stars" in m["description"]
    assert out["mission"]["id"] == m["id"]
    assert out["project"]["latest"]["id"] == m["id"]
    assert out["project"]["playable"] is True


def test_the_starter_game_tests_pass_in_the_new_project(env):
    import shutil
    if not shutil.which("node"):
        pytest.skip("node not installed")
    env.pj.create("Game", "web-game", "", start_mission=False)
    r = subprocess.run(["node", "--test"], cwd=env.tmp / "Orion Projects" / "Game", capture_output=True, text=True,
                       timeout=60)
    assert r.returncode == 0, r.stdout + r.stderr


@pytest.mark.parametrize("starter,files", [
    ("website", ["index.html", "style.css", "script.js"]),
    ("python-tool", ["tool.py", "test_tool.py"]),
    ("empty", ["NOTES.md"]),
])
def test_other_starters(env, starter, files):
    env.pj.create(f"P {starter}", starter, "an idea", start_mission=False)
    path = env.tmp / "Orion Projects" / f"P {starter}"
    for f in files + ["CLAUDE.md", "README.md"]:
        assert (path / f).is_file()
    assert env.calls == []


def test_refuses_duplicates_and_unknown_starters(env):
    env.pj.create("Twice", "empty", "", start_mission=False)
    with pytest.raises(ProjectError, match="already"):
        env.pj.create("Twice", "empty", "")
    (env.tmp / "Orion Projects" / "Taken").mkdir()     # a folder Orion didn't make is never touched
    with pytest.raises(ProjectError, match="already"):
        env.pj.create("Taken", "empty", "")
    assert (env.tmp / "Orion Projects" / "Taken").is_dir()
    with pytest.raises(ProjectError, match="starter"):
        env.pj.create("New", "rocket-ship", "")
    assert not (env.tmp / "Orion Projects" / "New").exists()


def test_project_is_kept_when_no_agent_can_start(tmp_path):
    mod, calls, _ = fake_missions(fail="No coding agent is installed.")
    pj = Projects(tmp_path / "d", root=tmp_path / "r", missions_mod=mod)
    out = pj.create("Kept", "empty", "something")
    assert out["mission"] is None
    assert "No coding agent is installed." in out["message"]
    assert (tmp_path / "r" / "Kept" / "CLAUDE.md").is_file()
    assert pj.list()[0]["name"] == "Kept"


def test_ask_for_a_change_starts_a_follow_up_on_the_same_folder(env):
    env.pj.create("Follow", "web-game", "a game")
    out = env.pj.ask_change("Follow", "Make the ship faster and add a high score")
    assert len(env.calls) == 2
    assert env.calls[1]["repo"] == env.calls[0]["repo"]
    assert env.calls[1]["title"].startswith("Make the ship faster")
    assert out["project"]["missions"] == [env.calls[0]["id"], env.calls[1]["id"]]
    with pytest.raises(ProjectError):
        env.pj.ask_change("Follow", "   ")
    with pytest.raises(ProjectError):
        env.pj.ask_change("Nope", "x")


def test_list_survives_a_restart(env):
    env.pj.create("Saved", "empty", "", start_mission=False)
    again = Projects(env.tmp / "data", root=env.tmp / "Orion Projects", missions_mod=fake_missions()[0])
    assert [p["name"] for p in again.list()] == ["Saved"]
    snap = again.snapshot()
    assert {s["id"] for s in snap["starters"]} == {"web-game", "website", "python-tool", "empty"}
    assert json.loads((env.tmp / "data" / "projects.json").read_text())["Saved"]["starter"] == "empty"


def test_open_folder_uses_the_opener(env):
    env.pj.create("Open", "empty", "", start_mission=False)
    env.pj.open_folder("Open")
    assert env.opened == [str(env.tmp / "Orion Projects" / "Open")]


def test_play_serves_on_localhost_only_and_hides_git(env):
    env.pj.create("Play", "web-game", "", start_mission=False)
    out = env.pj.play("Play")
    assert out["url"].startswith("http://127.0.0.1:") and out["url"].endswith("/index.html")
    assert env.browsed == [out["url"]]
    html = urllib.request.urlopen(out["url"], timeout=5).read().decode()
    assert "<canvas" in html
    base = out["url"].rsplit("/", 1)[0]
    for hidden in ("/.git/config", "/%2egit/config", "/.gitignore"):
        with pytest.raises(urllib.error.HTTPError):
            urllib.request.urlopen(base + hidden, timeout=5)
    assert env.pj.play("Play", open_browser=False)["url"] == out["url"]   # one server per folder


def test_play_prefers_the_unmerged_mission_copy(env):
    env.pj.create("Newer", "web-game", "")
    wt = env.tmp / "worktree"
    wt.mkdir()
    (wt / "index.html").write_text("<p>newest</p>")
    env.mc.missions[env.calls[0]["id"]]["worktree"] = str(wt)
    out = env.pj.play("Newer", open_browser=False)
    assert out["folder"] == str(wt)
    assert "newest" in urllib.request.urlopen(out["url"], timeout=5).read().decode()


def test_play_without_index_html_says_so(env):
    env.pj.create("Tool", "python-tool", "", start_mission=False)
    with pytest.raises(ProjectError, match="index.html"):
        env.pj.play("Tool")


def test_routes(env, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    daemon = types.SimpleNamespace(projects=env.pj)
    app = FastAPI()
    app.include_router(projects.make_router(lambda: daemon))
    c = TestClient(app)
    assert c.get("/projects").json()["projects"] == []
    r = c.post("/projects", json={"name": "Routed", "starter": "web-game", "idea": "fun"})
    assert r.status_code == 200 and r.json()["mission"]["id"]
    assert c.post("/projects", json={"name": "Routed", "starter": "web-game"}).status_code == 400
    assert c.post("/projects/Routed/change", json={"text": "bigger stars"}).json()["mission"]
    assert c.post("/projects/Routed/open").json()["ok"] is True
    assert c.post("/projects/Routed/play").json()["url"].startswith("http://127.0.0.1:")
    assert c.post("/projects/Missing/play").status_code == 400
