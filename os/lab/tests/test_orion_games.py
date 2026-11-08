"""orion-games (os/lab/bin/orion-games) against a fake Windows drive and a fake home: finding Steam, Epic and WoW
games (retail and a 1.12 client), copying a game for its launcher without touching the Windows drive, the anti-cheat
notes (offline and with a stand-in online lookup), play commands, and the GameMode/MangoHud recipe.

    python3 -m pytest os/lab/tests/test_orion_games.py -q
"""
from __future__ import annotations

import importlib.machinery
import importlib.util
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
BIN = REPO / "os/lab/bin/orion-games"
sys.dont_write_bytecode = True
_loader = importlib.machinery.SourceFileLoader("orion_games", str(BIN))
_spec = importlib.util.spec_from_loader("orion_games", _loader)
og = importlib.util.module_from_spec(_spec)
_loader.exec_module(og)


def acf(appid: str, name: str, idir: str) -> str:
    return f'"AppState"\n{{\n\t"appid"\t\t"{appid}"\n\t"name"\t\t"{name}"\n\t"installdir"\t\t"{idir}"\n\t"SizeOnDisk"\t\t"12"\n}}\n'


@pytest.fixture()
def world(tmp_path, monkeypatch):
    c = tmp_path / "media" / "C"            # the Windows drive (C:)
    d = tmp_path / "media" / "Data"         # a second drive (D:) with a Steam library
    (c / "Windows" / "System32").mkdir(parents=True)
    steam = c / "Program Files (x86)" / "Steam"
    (steam / "steamapps" / "common" / "Portal 2").mkdir(parents=True)
    (steam / "steamapps" / "common" / "Portal 2" / "portal2.exe").write_bytes(b"x" * 100)
    (steam / "steamapps" / "appmanifest_620.acf").write_text(acf("620", "Portal 2", "Portal 2"))
    (steam / "steamapps" / "appmanifest_228980.acf").write_text(acf("228980", "Steamworks Common Redistributables", "Steamworks Shared"))
    (steam / "steamapps" / "libraryfolders.vdf").write_text(
        '"libraryfolders"\n{\n\t"0"\n\t{\n\t\t"path"\t\t"C:\\\\Program Files (x86)\\\\Steam"\n\t}\n'
        '\t"1"\n\t{\n\t\t"path"\t\t"D:\\\\SteamLibrary"\n\t}\n}\n')
    lib2 = d / "SteamLibrary" / "steamapps"
    (lib2 / "common" / "Hades").mkdir(parents=True)
    (lib2 / "common" / "Hades" / "Hades.exe").write_bytes(b"h")
    (lib2 / "appmanifest_1145360.acf").write_text(acf("1145360", "Hades", "Hades"))
    man = c / "ProgramData" / "Epic" / "EpicGamesLauncher" / "Data" / "Manifests"
    man.mkdir(parents=True)
    rl = c / "Program Files" / "Epic Games" / "rocketleague"
    (rl / "Binaries").mkdir(parents=True)
    (rl / "Binaries" / "RocketLeague.exe").write_bytes(b"r" * 50)
    (man / "ABC.item").write_text(json.dumps({"AppName": "Sugar", "DisplayName": "Rocket League",
                                              "InstallLocation": "C:\\Program Files\\Epic Games\\rocketleague",
                                              "InstallSize": 50}))
    wow = c / "Program Files (x86)" / "World of Warcraft" / "_retail_"
    wow.mkdir(parents=True)
    (wow / "Wow.exe").write_bytes(b"w")
    client = c / "Games" / "Vanilla Client"
    (client / "Data").mkdir(parents=True)
    (client / "WoW.exe").write_bytes(b"v")
    (client / "realmlist.wtf").write_text("set realmlist logon.example.org\r\n")
    (client / "Data" / "dbc.MPQ").write_bytes(b"m" * 10)
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / ".config"))
    monkeypatch.setenv("ORION_GAMES_MOUNTS", f"{d}:{c}")       # order on purpose: C: must still come first
    fake_fp = tmp_path / "flatpak"
    fake_fp.write_text("#!/bin/sh\n[ \"$1\" = info ] && [ \"$2\" = com.valvesoftware.Steam ] && exit 0\nexit 1\n")
    fake_fp.chmod(0o755)
    monkeypatch.setattr(og, "FLATPAK", str(fake_fp))
    monkeypatch.delenv("ORION_GAMES_ONLINE", raising=False)
    return {"c": c, "d": d, "home": home, "tmp": tmp_path, "client": client}


def snapshot(p: Path) -> dict[str, tuple[int, int]]:
    return {str(f): (f.stat().st_size, f.stat().st_mtime_ns) for f in p.rglob("*") if f.is_file()}


def test_vdf_parser():
    d = og.parse_vdf('"a"\n{\n "b" "1"\n "c" { "d" "x\\\\y" }\n}\n')
    assert d == {"a": {"b": "1", "c": {"d": "x\\y"}}}


def test_finds_games_on_the_windows_drive(world):
    res = og.find_games()
    assert res["windows_roots"][0] == str(world["c"])
    ids = {g["id"]: g for g in res["windows"]}
    assert "steam:620" in ids and "steam:1145360" in ids and "steam:228980" not in ids
    assert ids["steam:1145360"]["path"].endswith("SteamLibrary/steamapps/common/Hades")
    rl = ids["epic:Sugar"]
    assert rl["name"] == "Rocket League" and rl["launcher"] == "heroic" and rl["path"].endswith("rocketleague")
    assert "Rocket League runs through Heroic" in rl["note"] and "Import game" in rl["reuse"]
    assert ids["wow:retail"]["launcher"] == "battlenet" and "retail" in ids["wow:retail"]["name"]
    assert "Blizzard doesn't support Linux" in ids["wow:retail"]["note"]
    v = ids["wow112:Vanilla Client"]
    assert v["store"] == "wow112" and "no anti-cheat" in v["note"]
    assert "read-only" in ids["steam:620"]["reuse"]
    assert res["launchers"]["steam"]["installed"] and not res["launchers"]["heroic"]["installed"]


def test_import_steam_copies_without_touching_windows(world, monkeypatch):
    lib = world["home"] / ".var/app/com.valvesoftware.Steam/.local/share/Steam/steamapps"
    lib.mkdir(parents=True)
    monkeypatch.setattr(og, "steam_running", lambda: False)
    before = snapshot(world["c"])
    g = next(g for g in og.find_games()["windows"] if g["id"] == "steam:620")
    r = og.import_game(g, say=lambda _m: None)
    assert r["ok"] and "Verifying" in r["message"]
    assert (lib / "common/Portal 2/portal2.exe").read_bytes() == b"x" * 100
    assert (lib / "appmanifest_620.acf").is_file()
    assert snapshot(world["c"]) == before
    res = og.find_games()
    assert any(h["id"] == "steam:620" for h in res["here"])
    assert next(g for g in res["windows"] if g["id"] == "steam:620")["copied"] is True
    monkeypatch.setattr(og, "steam_running", lambda: True)
    assert "Close Steam" in og.import_game(g, say=lambda _m: None)["message"]


def test_import_steam_needs_steam_opened_once(world):
    g = next(g for g in og.find_games()["windows"] if g["id"] == "steam:620")
    assert "Open Steam once" in og.import_game(g)["message"]


def test_import_epic_and_battlenet(world):
    res = og.find_games()
    rl = next(g for g in res["windows"] if g["id"] == "epic:Sugar")
    r = og.import_game(rl, say=lambda _m: None)
    assert r["ok"] and (world["home"] / "Games/Heroic/rocketleague/Binaries/RocketLeague.exe").is_file()
    assert "Import Game" in r["message"]
    wow = next(g for g in res["windows"] if g["id"] == "wow:retail")
    assert "Install Battle.net first" in og.import_game(wow)["message"]
    (world["home"] / "Games/battlenet/drive_c").mkdir(parents=True)
    r = og.import_game(wow, say=lambda _m: None)
    assert r["ok"] and (world["home"] / "Games/battlenet/drive_c/Program Files (x86)/World of Warcraft/_retail_/Wow.exe").is_file()


def test_copy_refuses_without_room(world, monkeypatch):
    monkeypatch.setattr(og.shutil, "disk_usage", lambda _p: type("U", (), {"free": 5})())
    r = og.copy_game(world["client"], world["home"] / "Games" / "x")
    assert not r["ok"] and "Not enough room" in r["message"]


def test_wow112_setup_copies_off_the_windows_drive(world, monkeypatch):
    calls = world["tmp"] / "wow.log"
    fake = world["tmp"] / "orion-wow-client"
    fake.write_text(f"#!/bin/sh\necho \"$*\" >> '{calls}'\n")
    fake.chmod(0o755)
    monkeypatch.setattr(og, "WOW_CLIENT", str(fake))
    rc = og.setup("wow112", str(world["client"]), say=lambda _m: None, interactive=False)
    assert rc == 0
    dst = world["home"] / "Games" / "Vanilla Client"
    assert (dst / "WoW.exe").is_file() and (dst / "realmlist.wtf").is_file()
    assert calls.read_text().strip() == f"setup {dst}"
    assert (world["client"] / "realmlist.wtf").read_bytes() == b"set realmlist logon.example.org\r\n"
    assert any(g["id"] == "wow112:Vanilla Client" for g in og.find_games()["here"])
    assert og.setup("wow112", str(world["tmp"]), say=lambda _m: None, interactive=False) == 1


def test_setup_installs_through_get_apps(world, monkeypatch):
    log = world["tmp"] / "apps.log"
    fake = world["tmp"] / "orion-apps"
    fake.write_text(f"#!/bin/sh\necho \"$*\" >> '{log}'\necho '{{\"ok\": true, \"message\": \"Installed.\"}}'\n")
    fake.chmod(0o755)
    monkeypatch.setattr(og, "APPS_CMD", str(fake))
    started = []
    monkeypatch.setattr(og, "spawn", started.append)
    out = []
    assert og.setup("heroic", say=out.append) == 0
    assert og.setup("battlenet", say=out.append) == 0
    assert og.setup("steam", say=out.append) == 0            # already installed: nothing to do
    text = log.read_text()
    assert "install com.heroicgameslauncher.hgl --json" in text and "install net.lutris.Lutris --json" in text
    assert "com.valvesoftware.Steam" not in text
    assert started == [[og.FLATPAK, "run", "net.lutris.Lutris", "lutris:battlenet"]]
    assert any("Enable Steam Play" in o for o in out)


def test_here_heroic_and_lutris(world):
    h = world["home"] / ".var/app/com.heroicgameslauncher.hgl/config/heroic/legendaryConfig/legendary"
    h.mkdir(parents=True)
    (h / "installed.json").write_text(json.dumps({"Sugar": {"title": "Rocket League", "install_path": "/g/rl"}}))
    db = world["home"] / ".var/app/net.lutris.Lutris/data/lutris"
    db.mkdir(parents=True)
    con = sqlite3.connect(db / "pga.db")
    con.execute("CREATE TABLE games (slug TEXT, name TEXT, runner TEXT, installed INTEGER)")
    con.execute("INSERT INTO games VALUES ('battlenet', 'Battle.net', 'wine', 1), ('old', 'Old', 'wine', 0)")
    con.commit()
    con.close()
    here = {g["id"]: g for g in og.find_games()["here"]}
    assert "epic:Sugar" in here and "lutris:battlenet" in here and "lutris:old" not in here
    assert next(g for g in og.find_games()["windows"] if g["id"] == "epic:Sugar")["copied"]


def test_play_commands(world, monkeypatch):
    started = []
    monkeypatch.setattr(og, "spawn", started.append)
    assert og.play("steam:620") == 0 and started[-1] == [og.FLATPAK, "run", "com.valvesoftware.Steam", "steam://rungameid/620"]
    assert og.play("epic:Sugar") == 0 and started[-1][-1] == "heroic://launch/legendary/Sugar"
    assert og.play("lutris:battlenet") == 0 and started[-1][-1] == "lutris:rungame/battlenet"
    assert og.play("steam:620;reboot") == 2 and len(started) == 3


def test_notes_offline_by_default_and_online_when_allowed(world, monkeypatch):
    assert og.online_allowed() is False
    g = {"id": "steam:252950", "name": "Rocket League", "appid": "252950"}

    def fetch(url):
        if "protondb" in url:
            return json.dumps({"tier": "gold", "total": 900}).encode()
        return json.dumps([{"name": "Rocket League", "status": "Running", "anticheats": ["Easy Anti-Cheat"]}]).encode()
    notes = og.online_notes(g, fetch)
    assert notes[0].startswith("ProtonDB: players rate it gold") and "Easy Anti-Cheat" in notes[1]

    def broken(url):
        raise OSError("no network")
    assert og.online_notes(g, broken) == []
    (world["home"] / ".config/orion").mkdir(parents=True)
    (world["home"] / ".config/orion/games.json").write_text('{"online_notes": true}')
    assert og.online_allowed() is True


def test_cli_list_json(world):
    env = dict(os.environ, ORION_FLATPAK="false", ORION_DUALBOOT_PY="/nonexistent")
    r = subprocess.run([sys.executable, str(BIN), "list", "--json"], capture_output=True, text=True, env=env,
                       stdin=subprocess.DEVNULL, timeout=60)
    data = json.loads(r.stdout)
    assert data["ok"] and len(data["windows"]) >= 5 and data["windows_drive"]["open"] is True


def test_game_extras_recipe():
    t = (REPO / "os/lab/recipes/game-extras.sh").read_text()
    assert "gamemode" in t and "org.freedesktop.Platform.VulkanLayer.MangoHud//$BRANCH" in t
    assert subprocess.run(["sh", "-n", str(REPO / "os/lab/recipes/game-extras.sh")]).returncode == 0
    assert subprocess.run(["bash", "-n", str(REPO / "os/dualboot/orion-windows-files")]).returncode == 0
    assert 'MODE="${1:-open}"' in (REPO / "os/dualboot/orion-windows-files").read_text()


def test_never_downloads_blizzard_files():
    t = BIN.read_text()
    assert "blizzard.com" not in t.lower() and "battle.net/download" not in t.lower()
