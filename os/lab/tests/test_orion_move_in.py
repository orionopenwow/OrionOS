"""orion-move-in (os/lab/bin/orion-move-in) against a fake OrionMove folder, plus the Windows side's data files and
PowerShell scripts (parse checks and the shared functions, when pwsh is installed).

    python3 -m pytest os/lab/tests -q
"""
from __future__ import annotations

import hashlib
import importlib.machinery
import importlib.util
import json
import os
import re
import shutil
import sqlite3
import struct
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
BIN = REPO / "os/lab/bin/orion-move-in"
WIN = REPO / "os/windows"

sys.dont_write_bytecode = True
_loader = importlib.machinery.SourceFileLoader("orion_move_in", str(BIN))
_spec = importlib.util.spec_from_loader("orion_move_in", _loader)
mi = importlib.util.module_from_spec(_spec)
_loader.exec_module(mi)

PASS = "correct " + "horse battery"          # test-only passphrase, built at runtime
KEY = "wifi-" + "test-" + "pw1"


def lz4_literals(data: bytes) -> bytes:
    """A valid LZ4 block holding only literals (enough to test the decoder's literal path)."""
    n = len(data)
    if n < 15:
        return bytes([n << 4]) + data
    out = bytearray([0xF0])
    rest = n - 15
    while rest >= 255:
        out.append(255)
        rest -= 255
    out.append(rest)
    return bytes(out) + data


def mozlz4(obj) -> bytes:
    raw = json.dumps(obj).encode()
    return b"mozLz40\0" + struct.pack("<I", len(raw)) + lz4_literals(raw)


def write_sums(move: Path) -> None:
    lines = []
    for f in sorted(move.rglob("*")):
        if f.is_file() and f.name != "SHA256SUMS":
            lines.append(f"{hashlib.sha256(f.read_bytes()).hexdigest()}  {f.relative_to(move).as_posix()}")
    (move / "SHA256SUMS").write_text("\n".join(lines) + "\n")


def make_move(root: Path, with_passwords: bool = True) -> Path:
    move = root / "OrionMove"
    (move / "files/Documents/Taxes").mkdir(parents=True)
    (move / "files/Documents/Taxes/2025.txt").write_text("numbers")
    (move / "files/Documents/notes.txt").write_text("from windows")
    (move / "files/Pictures").mkdir(parents=True)
    (move / "files/Pictures/cat.jpg").write_bytes(b"\xff\xd8cat")
    (move / "extra/WoW 1.12").mkdir(parents=True)
    (move / "extra/WoW 1.12/WoW.exe").write_bytes(b"MZ")
    (move / "extra/WoW 1.12/realmlist.wtf").write_text("set realmlist old.example\r\n")
    (move / "bookmarks").mkdir()
    chrome = {"roots": {"bookmark_bar": {"type": "folder", "name": "Bookmarks bar", "children": [
        {"type": "url", "name": "Orion <docs>", "url": "https://example.org/docs?a=1&b=2"},
        {"type": "url", "name": "bad", "url": "javascript:alert(1)"},
        {"type": "folder", "name": "Work", "children": [{"type": "url", "name": "Mail", "url": "https://mail.example.com"}]}]},
        "other": {"type": "folder", "name": "Other", "children": []}}}
    (move / "bookmarks/chrome-Default.json").write_text(json.dumps(chrome))
    ff = {"type": "text/x-moz-place-container", "root": "placesRoot", "children": [
        {"type": "text/x-moz-place-container", "root": "toolbarFolder", "title": "toolbar", "children": [
            {"type": "text/x-moz-place", "title": "Wiki", "uri": "https://wiki.example.net/"},
            {"type": "text/x-moz-place-separator"}]}]}
    (move / "bookmarks/firefox-abc.default-release.jsonlz4").write_bytes(mozlz4(ff))
    (move / "wifi").mkdir()
    (move / "wifi/networks.json").write_text(json.dumps([
        {"ssid": "Home", "auth": "WPA2PSK", "has_password": with_passwords},
        {"ssid": "Cafe", "auth": "open", "has_password": False},
        {"ssid": "Office", "auth": "WPA2", "has_password": False},
        {"ssid": "Neighbour", "auth": "WPA3SAE", "has_password": False},
        {"ssid": "Known", "auth": "WPA2PSK", "has_password": False}]))
    if with_passwords:
        plain = json.dumps({"networks": [{"ssid": "Home", "auth": "WPA2PSK", "key": KEY}]}).encode()
        r = subprocess.run(["openssl", "enc", "-aes-256-cbc", "-salt", "-pbkdf2", "-iter", "200000", "-md", "sha256",
                            "-pass", "env:P"], input=plain, capture_output=True, env=dict(os.environ, P=PASS))
        assert r.returncode == 0, r.stderr
        (move / "wifi/passwords.enc").write_bytes(r.stdout)
    (move / "apps").mkdir()
    (move / "apps/installed.json").write_text(json.dumps([
        {"name": "Discord", "match": "Discord", "kind": "linux", "flatpak": "com.discordapp.Discord", "how": "x"},
        {"name": "Visual Studio Code", "match": "Visual Studio Code", "kind": "linux", "recipe": "vscode", "how": "x"},
        {"name": "Turtle WoW", "match": "World of Warcraft 1.12 (private server)", "kind": "wine", "tool": "orion-wow-client",
         "how": "Bring your client folder"},
        {"name": "Roblox", "match": "Roblox", "kind": "none", "how": "Keep Windows for it."},
        {"name": "Claude", "match": "Claude", "kind": "web", "web": "https://claude.ai", "how": "Use claude.ai"},
        {"name": "Firefox", "match": "Firefox", "kind": "builtin", "how": "already"},
        {"name": "Bad", "match": "Bad", "kind": "linux", "flatpak": "evil; rm -rf /", "how": "x"}]))
    man = {"format": 1, "made": "2026-10-17T10:00:00+01:00", "computer": "TESTPC", "folders": ["Documents", "Pictures"],
           "extra": [{"name": "WoW 1.12", "wow_client": True}],
           "bookmarks": [{"browser": "chrome", "profile": "Default", "file": "bookmarks/chrome-Default.json",
                          "format": "chromium-json"},
                         {"browser": "firefox", "profile": "abc.default-release",
                          "file": "bookmarks/firefox-abc.default-release.jsonlz4", "format": "mozlz4-json"},
                         {"browser": "edge", "profile": "evil", "file": "../../etc/passwd", "format": "chromium-json"}],
           "wifi": {"networks": "wifi/networks.json", "passwords": "wifi/passwords.enc" if with_passwords else None},
           "apps": "apps/installed.json", "boardroom": None}
    (move / "manifest.json").write_text(json.dumps(man))
    write_sums(move)
    return move


@pytest.fixture
def env(tmp_path, monkeypatch):
    h = tmp_path / "home"
    h.mkdir()
    monkeypatch.setenv("HOME", str(h))
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.setenv("XDG_STATE_HOME", str(h / ".local/state"))
    drive = tmp_path / "media/usb"
    drive.mkdir(parents=True)
    monkeypatch.setenv("ORION_MOVE_SEARCH", str(tmp_path / "media"))
    return {"home": h, "drive": drive, "tmp": tmp_path}


def test_finds_and_describes(env):
    move = make_move(env["drive"])
    assert mi.find_moves() == [move]
    s = mi.summary(move)
    assert s["files"]["Documents"]["files"] == 2 and s["computer"] == "TESTPC"
    assert s["wifi_passwords"] is True and "Home" in s["wifi_networks"]
    text = mi.describe(s)
    assert "Documents: 2 files" in text and "WoW client" in text


def test_verify_spots_damage(env):
    move = make_move(env["drive"])
    assert mi.verify(move)["bad"] == []
    (move / "files/Pictures/cat.jpg").write_bytes(b"broken")
    (move / "files/Documents/notes.txt").unlink()
    assert mi.verify(move)["bad"] == ["files/Documents/notes.txt", "files/Pictures/cat.jpg"]


def test_files_never_overwrite(env):
    move = make_move(env["drive"])
    h = env["home"]
    (h / ".config").mkdir()
    (h / ".config/user-dirs.dirs").write_text('XDG_DOCUMENTS_DIR="$HOME/Docs"\nXDG_PICTURES_DIR="$HOME/Pictures"\n')
    (h / "Docs").mkdir()
    (h / "Docs/notes.txt").write_text("my own linux notes")
    (move / "files/Pictures/cat.jpg").write_bytes(b"damaged")          # no longer matches SHA256SUMS
    r = mi.move_files(move, mi.verify(move)["bad"])
    assert (h / "Docs/notes.txt").read_text() == "my own linux notes"
    assert (h / "Docs/notes (from Windows).txt").read_text() == "from windows"
    assert (h / "Docs/Taxes/2025.txt").read_text() == "numbers"
    assert not (h / "Pictures/cat.jpg").exists() and r["damaged"] == 1
    assert (h / "Games/WoW 1.12/WoW.exe").is_file()                    # a WoW client goes to ~/Games
    again = mi.move_files(move)
    assert again["copied"] == 1 and again["same"] >= 4                 # only the (now unskipped) picture is new
    assert not list((h / "Docs").glob("notes (from Windows 2)*"))
    assert (move / "files/Documents/notes.txt").read_text() == "from windows"   # the drive is untouched


def test_bookmarks_html_and_firefox_import(env):
    move = make_move(env["drive"])
    groups = mi.load_bookmarks(move)
    assert [g["title"] for g in groups] == ["Chrome (Default)", "Firefox (abc.default-release)"]
    assert mi.count_links(groups) == 3                                   # javascript: dropped, ../ file ignored
    html = mi.bookmarks_html(groups)
    assert "https://example.org/docs?a=1&amp;b=2" in html and "Orion &lt;docs&gt;" in html
    assert "javascript:" not in html and "Bookmarks toolbar" in html
    # Firefox profile with nothing of the user's yet -> imported on its next start
    ff = env["home"] / ".mozilla/firefox"
    prof = ff / "abcd.default-release"
    prof.mkdir(parents=True)
    (ff / "profiles.ini").write_text("[Install4F96D1932A9F858E]\nDefault=abcd.default-release\nLocked=1\n\n"
                                     "[Profile0]\nName=default-release\nIsRelative=1\nPath=abcd.default-release\n")
    con = sqlite3.connect(prof / "places.sqlite")
    con.executescript("CREATE TABLE moz_places (id INTEGER PRIMARY KEY, url TEXT);"
                      "CREATE TABLE moz_bookmarks (id INTEGER PRIMARY KEY, type INTEGER, fk INTEGER);"
                      "INSERT INTO moz_places VALUES (1, 'https://www.mozilla.org/firefox/central/');"
                      "INSERT INTO moz_bookmarks VALUES (1, 1, 1);")
    con.commit()
    con.close()
    r = mi.move_bookmarks(move)
    assert r["links"] == 3 and r["firefox"] == "on-next-start"
    out = env["home"] / "Documents/Bookmarks from Windows.html"
    assert out.is_file()
    prefs = (prof / "prefs.js").read_text()
    assert 'browser.places.importBookmarksHTML", true' in prefs and str(out) in prefs
    # once the user has bookmarks of their own, Firefox is left alone
    con = sqlite3.connect(prof / "places.sqlite")
    con.executescript("INSERT INTO moz_places VALUES (2, 'https://news.example.com/');"
                      "INSERT INTO moz_bookmarks VALUES (2, 1, 2);")
    con.commit()
    con.close()
    (prof / "prefs.js").unlink()
    r = mi.move_bookmarks(move)
    assert r["firefox"] == "manual" and not (prof / "prefs.js").exists()
    assert r["file"].endswith("Bookmarks from Windows (from Windows).html")


def test_lz4_with_a_match():
    # "abcabcabcabc": 3 literals then a match of 9 at offset 3
    block = bytes([(3 << 4) | (9 - 4)]) + b"abc" + bytes([3, 0])
    assert mi.lz4_block(block, 12) == b"abcabcabcabc"


@pytest.fixture
def fake_nmcli(env, monkeypatch):
    log = env["tmp"] / "nmcli.log"
    script = env["tmp"] / "nmcli"
    script.write_text("#!/bin/sh\nprintf '%s\\n' \"$*\" >> '" + str(log) + "'\n"
                      "case \"$*\" in *'connection show'*) printf 'Known:802-11-wireless\\nWired:802-3-ethernet\\n' ;; esac\n")
    script.chmod(0o755)
    monkeypatch.setattr(mi, "NMCLI", str(script))
    return log


@pytest.mark.skipif(not shutil.which("openssl"), reason="needs openssl")
def test_wifi_with_and_without_passwords(env, fake_nmcli):
    move = make_move(env["drive"])
    with pytest.raises(ValueError):
        mi.read_wifi_passwords(move, "wrong " + "passphrase")
    assert mi.read_wifi_passwords(move, PASS)[0]["key"] == KEY
    r = mi.move_wifi(move, PASS)
    assert r["added"] == ["Home", "Cafe"] and r["already"] == ["Known"]
    assert r["skipped"] == ["Office"] and r["names_only"] == ["Neighbour"]
    calls = fake_nmcli.read_text()
    assert f"con-name Home ssid Home wifi-sec.key-mgmt wpa-psk wifi-sec.psk {KEY}" in calls
    assert "con-name Cafe ssid Cafe" in calls and "Cafe wifi-sec" not in calls
    fake_nmcli.write_text("")
    r = mi.move_wifi(move, None)                                         # names only: nothing secret needed
    assert r["added"] == ["Cafe"] and "Home" in r["names_only"]
    assert "psk" not in fake_nmcli.read_text()


def test_wifi_decrypts_what_windows_makes(env, tmp_path):
    """The PowerShell side's Protect-OrionBytes and the importer's decrypt() agree."""
    pwsh = shutil.which("pwsh")
    if not pwsh:
        pytest.skip("needs pwsh")
    out = tmp_path / "p.enc"
    cmd = (f". '{WIN / 'Move-ToOrion.ps1'}'; $b = Protect-OrionBytes ([Text.Encoding]::UTF8.GetBytes('{{\"networks\":[]}}')) "
           f"$env:P; [IO.File]::WriteAllBytes('{out}', $b)")
    r = subprocess.run([pwsh, "-NoProfile", "-Command", cmd], capture_output=True, text=True,
                       env=dict(os.environ, P=PASS), timeout=120)
    assert r.returncode == 0, r.stderr
    assert json.loads(mi.decrypt(out.read_bytes(), PASS)) == {"networks": []}


def test_app_offers_and_install(env, monkeypatch):
    move = make_move(env["drive"])
    offers = mi.app_offers(move)
    keys = [o["key"] for o in offers]
    assert keys == ["flatpak:com.discordapp.Discord", "recipe:vscode", "recipe:windows-apps"]
    notes = mi.app_notes(move)
    assert any(n.startswith("Roblox:") for n in notes) and any("orion-wow-client" in n or "client folder" in n for n in notes)
    log = env["tmp"] / "calls.log"
    fake = env["tmp"] / "fake"
    fake.write_text("#!/bin/sh\nprintf '%s\\n' \"$0 $*\" >> '" + str(log) + "'\n"
                    "case \"$1\" in install) echo '{\"ok\": true, \"message\": \"Discord is installed.\"}' ;; esac\n")
    fake.chmod(0o755)
    monkeypatch.setattr(mi, "APPS_CMD", str(fake))
    monkeypatch.setattr(mi, "SYSTEMCTL", str(fake))
    assert mi.install_offer(offers[0]) == (True, "Discord is installed.")
    assert mi.install_offer(offers[1])[0] is True
    text = log.read_text()
    assert "install com.discordapp.Discord --json" in text and "start orion-recipe@vscode.service" in text
    assert mi.pick_numbers("1 3", 3) == [0, 2] and mi.pick_numbers("all", 2) == [0, 1]
    assert mi.pick_numbers("2-3, 9", 3) == [1, 2] and mi.pick_numbers("", 3) == []


def test_cli_json(env):
    make_move(env["drive"])
    r = subprocess.run([sys.executable, str(BIN), "find", "--json"], capture_output=True, text=True,
                       env=dict(os.environ, ORION_MOVE_SEARCH=os.environ["ORION_MOVE_SEARCH"]))
    assert json.loads(r.stdout)["found"] == [str(env["drive"] / "OrionMove")]
    r = subprocess.run([sys.executable, str(BIN), "--json"], capture_output=True, text=True, stdin=subprocess.DEVNULL)
    data = json.loads(r.stdout)
    assert data["ok"] and data["files"]["copied"] >= 4 and data["bookmarks"]["links"] == 3
    assert (env["home"] / "Documents/notes.txt").is_file()


def test_unsafe_paths_rejected():
    for bad in ("../x", "/etc/passwd", "C:/Windows", "a/../../b", ""):
        assert mi.safe_rel(bad) is None
    assert mi.safe_rel("files/Documents/a.txt").as_posix() == "files/Documents/a.txt"


# ===================================================================================================== Windows side
def test_app_map_is_sound():
    m = json.loads((WIN / "app-map.json").read_text(encoding="utf-8"))
    kinds = set(m["kinds"])
    names = set()
    for a in m["apps"]:
        assert a["kind"] in kinds, a
        assert a["name"] not in names
        names.add(a["name"])
        assert a["how"] and a["match"]
        for pat in a["match"]:
            re.compile(pat, re.I)
        if a.get("flatpak"):
            assert re.match(r"^[A-Za-z][A-Za-z0-9_-]*(\.[A-Za-z0-9_-]+){2,}$", a["flatpak"]), a
        if a.get("recipe"):
            assert (REPO / f"os/lab/recipes/{a['recipe']}.sh").is_file(), a["recipe"]
        if a.get("tool"):
            assert (REPO / f"os/lab/bin/{a['tool']}").is_file(), a["tool"]
    for pat in m["ignore"]:
        re.compile(pat, re.I)
    for want in ("Google Chrome", "Discord", "Steam", "Battle.net", "Visual Studio Code", "Cursor", "Antigravity",
                 "Claude", "Microsoft Office", "Adobe Photoshop", "OBS Studio", "Spotify", "Oracle VirtualBox",
                 "World of Warcraft 1.12 (private server)", "MySQL Workbench", "Blender", "7-Zip"):
        assert want in names


def test_hardware_map_is_sound():
    m = json.loads((WIN / "hardware-map.json").read_text(encoding="utf-8"))
    kinds = {"wifi", "ethernet", "bluetooth", "gpu", "sound", "touchpad", "fingerprint"}
    for r in m["rules"]:
        assert r["kind"] in kinds and r["status"] in ("good", "firmware", "problem") and r["note"]
        if r.get("vendor"):
            assert re.match(r"^[0-9a-f]{4}$", r["vendor"])
        for d in r.get("device", []):
            assert re.match(r"^[0-9a-f]{4}$", d)
        if r["kind"] == "gpu":
            assert r["flavour"] in ("standard", "nvidia")
    for k in kinds - {"ethernet"}:      # every kind ends in a catch-all rule
        assert any(r["kind"] == k and not r.get("vendor") and not r.get("acpi") for r in m["rules"]), k


PS_FILES = sorted(WIN.glob("*.ps1"))


@pytest.mark.parametrize("ps", PS_FILES, ids=[p.name for p in PS_FILES])
def test_powershell_parses_and_is_crlf(ps):
    raw = ps.read_bytes()
    if ps.name in ("Check-OrionReady.ps1", "Move-ToOrion.ps1"):
        assert b"\r\n" in raw and raw.count(b"\n") == raw.count(b"\r\n"), "CRLF line endings (.gitattributes)"
        assert all(b < 128 for b in raw), "ASCII only (Windows PowerShell 5.1 reads files without a BOM as ANSI)"
    pwsh = shutil.which("pwsh")
    if not pwsh:
        pytest.skip("needs pwsh")
    cmd = ("$e = $null; [void][System.Management.Automation.Language.Parser]::ParseFile($env:F, [ref]$null, [ref]$e); "
           "if ($e) { $e | ForEach-Object { $_.ToString() }; exit 1 }")
    r = subprocess.run([pwsh, "-NoProfile", "-Command", cmd], capture_output=True, text=True,
                       env=dict(os.environ, F=str(ps)), timeout=120)
    assert r.returncode == 0, r.stdout + r.stderr


def test_readiness_functions_in_pwsh():
    pwsh = shutil.which("pwsh")
    if not pwsh:
        pytest.skip("needs pwsh")
    script = r"""
. $env:F
$hw = Read-OrionMap "hardware-map.json"; $map = Read-OrionMap "app-map.json"
$o = @{}
$id = ConvertTo-OrionId "PCI\VEN_14C3&DEV_7902&SUBSYS_1"; $o.mt7902 = (Find-OrionRule $hw.rules (Get-OrionKind "Net" "MediaTek Wi-Fi 6E MT7902" "PCI\VEN_14C3&DEV_7902&SUBSYS_1") $id).status
$id = ConvertTo-OrionId "PCI\VEN_10DE&DEV_28E0"; $o.nvidia = (Find-OrionRule $hw.rules "gpu" $id).flavour
$id = ConvertTo-OrionId "ACPI\CSC3551\0"; $o.cirrus = (Find-OrionRule $hw.rules (Get-OrionKind "System" "amp" "ACPI\CSC3551\0") $id).status
$o.vcpp = Find-OrionApp $map "Microsoft Visual C++ 2015-2022 Redistributable (x64)"
$o.code = (Find-OrionApp $map "Microsoft Visual Studio Code (User)").name
$plan = Get-OrionAppPlan $map @([pscustomobject]@{name="Roblox Player"}, [pscustomobject]@{name="Discord"})
$wifi = @([pscustomobject]@{kind="wifi"; status="good"; name="AX201"})
$o.replace = (Get-OrionVerdict @{Uefi=$true; RamGB=16; DiskGB=500; FreeGB=300; UnallocGB=0; Devices=$wifi; Apps=@($plan[1])}).Level
$o.side = (Get-OrionVerdict @{Uefi=$true; RamGB=16; DiskGB=500; FreeGB=300; UnallocGB=0; Devices=$wifi; Apps=$plan}).Level
$o.keep = (Get-OrionVerdict @{Uefi=$false; RamGB=16; DiskGB=500; FreeGB=300; UnallocGB=0; Devices=$wifi; Apps=@()}).Verdict
$o | ConvertTo-Json
"""
    r = subprocess.run([pwsh, "-NoProfile", "-Command", script], capture_output=True, text=True,
                       env=dict(os.environ, F=str(WIN / "Check-OrionReady.ps1")), timeout=120)
    assert r.returncode == 0, r.stderr
    o = json.loads(r.stdout)
    assert o["mt7902"] == "problem" and o["nvidia"] == "nvidia" and o["cirrus"] == "firmware"
    assert o["vcpp"] == "ignore" and o["code"] == "Visual Studio Code"
    assert o["replace"] == "replace" and o["side"] == "side"
    assert o["keep"].startswith("Keep Windows for now because") and "UEFI" in o["keep"]
