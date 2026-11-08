"""Bring my stuff (orion-move-in bring / browser / userscripts, and the first-run step) against a fake move kit made
the way Export-ForOrion.ps1 makes one (synthetic data only), with stand-ins for Get apps, Firefox, xdg-settings and
addons.mozilla.org.

    python3 -m pytest os/lab/tests/test_bring_my_stuff.py -q
"""
from __future__ import annotations

import importlib.machinery
import importlib.util
import io
import json
import sys
import zipfile
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
BIN = REPO / "os/lab/bin/orion-move-in"
sys.dont_write_bytecode = True
_loader = importlib.machinery.SourceFileLoader("orion_move_in_bring", str(BIN))
_spec = importlib.util.spec_from_loader("orion_move_in_bring", _loader)
mi = importlib.util.module_from_spec(_spec)
_loader.exec_module(mi)

US = "// ==UserScript==\n// @name  {n}\n// @version 2\n// ==/UserScript==\nconsole.log(1);\n"


def xpi(gid: str, signed: bool = True) -> bytes:
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w") as z:
        z.writestr("manifest.json", json.dumps({"manifest_version": 2, "name": "x",
                                                "browser_specific_settings": {"gecko": {"id": gid}}}))
        if signed:
            z.writestr("META-INF/mozilla.rsa", b"sig")
    return b.getvalue()


@pytest.fixture()
def kit(tmp_path, monkeypatch):
    move = tmp_path / "C" / "OrionMove"
    (move / "apps").mkdir(parents=True)
    (move / "browser" / "userscripts").mkdir(parents=True)
    ranked = {"usage_known": True, "apps": [
        {"name": "Microsoft Visual Studio Code", "match": "Visual Studio Code", "kind": "linux", "recipe": "vscode",
         "flatpak": "", "runs": 300, "used": True, "install": True},
        {"name": "Discord", "match": "Discord", "kind": "linux", "flatpak": "com.discordapp.Discord", "runs": 50,
         "used": True, "install": True},
        {"name": "VLC media player", "match": "VLC", "kind": "linux", "flatpak": "org.videolan.VLC", "runs": 0,
         "used": False, "install": True},
        {"name": "7-Zip", "match": "7-Zip", "kind": "builtin", "flatpak": "", "runs": 9, "used": True, "install": False}]}
    (move / "apps/ranked.json").write_text(json.dumps(ranked))
    (move / "apps/installed.json").write_text("[]")
    exts = [{"id": "c" * 32, "name": "uBlock Origin", "firefox": "install", "firefox_name": "uBlock Origin", "amo": "ublock-origin"},
            {"id": "d" * 32, "name": "Tampermonkey", "firefox": "install", "firefox_name": "Tampermonkey", "amo": "tampermonkey"},
            {"id": "e" * 32, "name": "Bad Slug", "firefox": "install", "amo": "../../etc"},
            {"id": "f" * 32, "name": "Honey", "firefox": "skip", "note": "Left out on purpose."},
            {"id": "g" * 32, "name": "Mystery <b>", "firefox": "unknown"}]
    (move / "browser/extensions.json").write_text(json.dumps(exts))
    (move / "browser/userscripts/Hello & Bye.user.js").write_text(US.format(n="Hello & Bye"))
    (move / "browser/userscripts/Two.user.js").write_text(US.format(n="Two"))
    (move / "browser/userscripts.json").write_text(json.dumps([{"file": "Hello & Bye.user.js", "name": "Hello & Bye", "version": "2"},
                                                               {"file": "Two.user.js", "name": "Two"}]))
    (move / "browser/search-engines.json").write_text(json.dumps([{"name": "Startpage", "url": "https://sp.example/?q={searchTerms}", "default": True}]))
    (move / "browser/bookmarks-chrome-Default.html").write_text("<!DOCTYPE NETSCAPE-Bookmark-file-1>\n<DL><p></DL>\n")
    (move / "wifi").mkdir()
    (move / "wifi/networks.json").write_text("[]")
    (move / "manifest.json").write_text(json.dumps({
        "format": 1, "made": "2026-11-07T10:00:00", "computer": "PC", "folders": [], "bookmarks": [], "kit_only": True,
        "wifi": {"networks": "wifi/networks.json"}, "apps": "apps/installed.json",
        "kit": {"apps_ranked": "apps/ranked.json", "extensions": "browser/extensions.json",
                "userscripts": "browser/userscripts.json", "search_engines": "browser/search-engines.json"}}))
    home = tmp_path / "home"
    prof = home / ".mozilla/firefox/abc.default-release"
    prof.mkdir(parents=True)
    (prof / "prefs.js").write_text("")
    (home / ".mozilla/firefox/profiles.ini").write_text("[Profile0]\nName=default-release\nIsRelative=1\n"
                                                       "Path=abc.default-release\nDefault=1\n")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / ".config"))
    monkeypatch.setenv("XDG_STATE_HOME", str(home / ".local/state"))
    calls = tmp_path / "calls.log"
    fail = tmp_path / "fail-discord"
    fake = tmp_path / "fake"
    fake.write_text(f"#!/bin/sh\nprintf '%s\\n' \"$(basename $0) $*\" >> '{calls}'\n"
                    f"case \"$*\" in *Discord*) [ -f '{fail}' ] && {{ echo '{{\"ok\": false, \"message\": \"No network.\"}}'; exit 1; }} ;; esac\n"
                    "echo '{\"ok\": true, \"message\": \"Installed.\"}'\n")
    fake.chmod(0o755)
    monkeypatch.setattr(mi, "APPS_CMD", str(fake))
    monkeypatch.setattr(mi, "SYSTEMCTL", str(fake))
    monkeypatch.setattr(mi, "XDG_SETTINGS", str(fake))
    monkeypatch.setattr(mi, "FIREFOX", str(tmp_path / "no-firefox"))
    monkeypatch.setattr(mi, "GATE", str(tmp_path / "no-gate"))
    fetched = []

    def fetch(url):
        fetched.append(url)
        if "/ublock-origin/" in url:
            return xpi("uBlock0@raymondhill.net")
        if "/tampermonkey/" in url:
            return xpi("firefox@tampermonkey.net")
        raise OSError("no")
    return {"move": move, "home": home, "prof": prof, "calls": calls, "fail": fail, "fetch": fetch, "fetched": fetched}


def test_ranked_offers_most_used_first(kit):
    offers = mi.ranked_offers(kit["move"])
    assert [o["key"] for o in offers] == ["recipe:vscode", "flatpak:com.discordapp.Discord"]
    assert mi.unused_apps(kit["move"]) == ["VLC"]
    s = mi.bring_summary(kit["move"])
    assert "Apps to install (2, most used first): Visual Studio Code; Discord" in s
    assert "Firefox extensions: 2 to add, 2 for you to look up" in s and "Userscripts for Tampermonkey: 2" in s


def test_ranked_offers_without_usage_installs_every_mapped_app(kit):
    r = json.loads((kit["move"] / "apps/ranked.json").read_text())
    r["usage_known"] = False
    (kit["move"] / "apps/ranked.json").write_text(json.dumps(r))
    assert "flatpak:org.videolan.VLC" in [o["key"] for o in mi.ranked_offers(kit["move"])]


def test_xpi_checks():
    assert mi.xpi_id(xpi("a@b.c")) == "a@b.c"
    assert mi.xpi_id(xpi("a@b.c", signed=False)) is None
    assert mi.xpi_id(xpi("../../evil")) is None
    assert mi.xpi_id(b"not a zip") is None


def test_extensions_added_to_the_profile(kit):
    r = mi.install_extensions(kit["move"], kit["prof"], kit["fetch"])
    ext = kit["prof"] / "extensions"
    assert sorted(p.name for p in ext.iterdir()) == ["firefox@tampermonkey.net.xpi", "uBlock0@raymondhill.net.xpi"]
    assert r["added"] == ["uBlock Origin", "Tampermonkey"] and r["ok"]
    assert all(u.startswith("https://addons.mozilla.org/firefox/downloads/latest/") for u in kit["fetched"])
    assert not any("etc" in u for u in kit["fetched"])             # a bad slug is never fetched
    r2 = mi.install_extensions(kit["move"], kit["prof"], kit["fetch"])
    assert r2["already"] == ["uBlock Origin", "Tampermonkey"] and not r2["added"]
    r3 = mi.install_extensions(kit["move"], None, kit["fetch"])
    assert not r3["ok"] and "Open Firefox once" in r3["message"]


def test_userscripts_page(kit):
    r = mi.stage_userscripts(kit["move"])
    d = mi.userscripts_dir()
    assert r["scripts"] == 2 and (d / "Two.user.js").is_file()
    page = (d / "index.html").read_text()
    assert 'href="Hello%20%26%20Bye.user.js">Install</a> Hello &amp; Bye' in page
    assert "addons.mozilla.org/firefox/addon/tampermonkey/" in page
    assert "Mystery &lt;b&gt;" in page and "<b>" not in page.split("<h2>Extensions")[1].split("</ul>")[0]
    assert "Passwords were never copied" in page and "Import from file" in page


def test_bring_all_resumes_after_an_interruption(kit, monkeypatch):
    kit["fail"].write_text("x")                                     # Discord fails the first time
    out = []
    r = mi.run_bring(kit["move"], assume_yes=True, fetch=kit["fetch"], out=out.append)
    assert not r["ok"] and r["pending"] == ["Installing your apps"]
    calls = kit["calls"].read_text()
    assert "start orion-recipe@vscode.service" in calls and "install com.discordapp.Discord --json" in calls
    assert "set default-web-browser firefox.desktop" in calls
    assert (kit["prof"] / "extensions/uBlock0@raymondhill.net.xpi").is_file()
    assert any(l.strip().startswith("[5/6] Installing your apps") for l in out)
    st = json.loads(mi.progress_path().read_text())
    assert "recipe:vscode" in st["apps_done"] and "browser" in st["done"]
    kit["fail"].unlink()
    kit["calls"].write_text("")
    out = []
    r = mi.run_bring(kit["move"], assume_yes=True, fetch=kit["fetch"], out=out.append)
    assert r["ok"]
    calls = kit["calls"].read_text()
    assert "Discord" in calls and "vscode" not in calls and "default-web-browser" not in calls   # only what was left
    assert any("Picking up where you left off" in l for l in out)
    # bookmarks from the kit's HTML (a kit-only folder has no raw bookmark files)
    docs = list((kit["home"] / "Documents").glob("Bookmarks from Windows*.html"))
    assert docs and "browser.bookmarks.file" in (kit["prof"] / "prefs.js").read_text()


def test_bring_can_be_skipped(kit):
    r = mi.run_bring(kit["move"], ask_fn=lambda q, d: False, out=lambda _m: None)
    assert r["skipped"] and not kit["calls"].exists()


def test_kit_on_the_windows_drive_is_found(kit, monkeypatch):
    monkeypatch.setenv("ORION_MOVE_SEARCH", str(kit["home"] / "nothing"))
    opened = []
    monkeypatch.setattr(mi, "windows_drive_present", lambda: True)
    monkeypatch.setattr(mi, "open_windows_drive", lambda: opened.append(1) or monkeypatch.setenv(
        "ORION_MOVE_SEARCH", str(kit["move"].parent.parent)) or True)
    monkeypatch.setattr(mi, "ask", lambda q, d=True: True)
    monkeypatch.setattr(mi.shutil, "which", lambda _x: None)
    assert mi.choose_move(None, interactive=True) == kit["move"]
    assert opened == [1]


def test_firstrun_offers_bring_my_stuff():
    t = (REPO / "os/lab/bin/orion-firstrun").read_text()
    assert "orion-move-in bring" in t and "orion-move-in windows-drive" in t and "Skip" in t
    assert "orion-move-in bring" in (REPO / "os/settings/orion_settings/pages.py").read_text()
