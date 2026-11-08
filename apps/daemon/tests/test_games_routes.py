"""games_routes.py: the HUD's Games window. Listing runs `orion-games list --json`; play starts only a found game
id; setup and import open a terminal (they ask before installing or copying). A stand-in orion-games is used."""
from __future__ import annotations

import json
import subprocess

import games_routes as G


def test_no_cli_means_a_plain_message(monkeypatch):
    monkeypatch.setattr(G, "cli", lambda: None)
    assert G.list_games()["ok"] is False
    assert G.do("play", "steam:1")["ok"] is False


def test_list_and_do(monkeypatch):
    monkeypatch.setattr(G, "cli", lambda: "/x/orion-games")
    seen = {}

    def runner(argv, **kw):
        seen["argv"] = argv
        return subprocess.CompletedProcess(argv, 0, json.dumps({"ok": True, "windows": [], "here": []}), "")
    assert G.list_games(runner)["ok"] and seen["argv"] == ["/x/orion-games", "list", "--json"]

    started, terms = [], []
    popen = lambda argv, **kw: started.append(argv)
    term = lambda cmd: terms.append(cmd) or True
    assert G.do("play", "steam:252950", term, popen)["ok"]
    assert started == [["/x/orion-games", "play", "steam:252950"]]
    assert not G.do("play", "steam:1; rm -rf ~", term, popen)["ok"]           # only ids orion-games makes
    assert G.do("setup-heroic", "", term, popen)["ok"] and terms[-1] == ["/x/orion-games", "setup", "heroic"]
    assert G.do("import", "epic:Sugar", term, popen)["ok"] and terms[-1] == ["/x/orion-games", "import", "epic:Sugar"]
    assert not G.do("format-disk", "", term, popen)["ok"]
    assert len(started) == 1


def test_router_routes():
    r = G.make_router(lambda: None)
    paths = {(rt.path, tuple(sorted(rt.methods))) for rt in r.routes}
    assert ("/games", ("GET",)) in paths and ("/games/do", ("POST",)) in paths
