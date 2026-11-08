"""Get apps: CLI JSON output and the safe-update / rollback logic, against a stand-in flatpak (fake_flatpak.py).

    python3 -m pytest os/getapps/tests -q
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
GETAPPS = HERE.parent
sys.path.insert(0, str(GETAPPS))
import orion_apps  # noqa: E402

RT = "runtime/org.freedesktop.Platform/x86_64/24.08"


def world(**over):
    s = {
        "apps": {
            "com.spotify.Client": {"name": "Spotify", "version": "1.2.0", "commit": "sp1", "runtime": RT},
            "org.videolan.VLC": {"name": "VLC", "version": "3.0.20", "commit": "vlc1", "runtime": RT},
        },
        "runtimes": {RT: "rt1"},
        "remote": {
            "com.spotify.Client": {"name": "Spotify", "version": "1.2.5", "commit": "sp2", "runtime": RT,
                                   "summary": "Music and podcasts"},
            "org.videolan.VLC": {"name": "VLC", "version": "3.0.21", "commit": "vlc2", "runtime": RT,
                                 "summary": "Plays almost any video"},
            "org.localsend.localsend_app": {"name": "LocalSend", "version": "1.15", "commit": "ls1",
                                            "runtime": RT, "summary": "Share files nearby"},
        },
        "remote_runtimes": {RT: "rt1"},
    }
    s.update(over)
    return s


@pytest.fixture()
def env(tmp_path, monkeypatch):
    state = tmp_path / "flatpak.json"
    stub = tmp_path / "flatpak"
    stub.write_text(f"#!/bin/sh\nexec {sys.executable} {HERE / 'fake_flatpak.py'} \"$@\"\n")
    stub.chmod(0o755)
    e = {"FAKE_FLATPAK_STATE": str(state), "ORION_FLATPAK": str(stub),
         "ORION_APPS_HISTORY": str(tmp_path / "state" / "apps.jsonl"),
         "ORION_APPS_CONFIG": str(tmp_path / "apps.json"), "ORION_APPS_NO_NOTIFY": "1",
         "ORION_APPS_NO_DAEMON": "1"}
    for k, v in e.items():
        monkeypatch.setenv(k, v)

    class Env:
        path = tmp_path

        def set(self, **over):
            state.write_text(json.dumps(world(**over)))

        def state(self):
            return json.loads(state.read_text())

        def cli(self, *args):
            r = subprocess.run([sys.executable, str(GETAPPS / "orion_apps.py"), *args], capture_output=True,
                               text=True, env={**os.environ, **e}, timeout=60)
            return r

        def json(self, *args):
            r = self.cli(*args, "--json")
            assert r.stdout, r.stderr
            return json.loads(r.stdout), r.returncode

        def history(self):
            return orion_apps.read_history()

    x = Env()
    x.set()
    return x


# ------------------------------------------------------------------------------------------------ JSON output
def test_updates_json_lists_waiting_apps(env):
    data, code = env.json("updates")
    assert code == 0
    assert [u["id"] for u in data["updates"]] == ["com.spotify.Client", "org.videolan.VLC"]
    assert data["updates"][0]["version"] == "1.2.5"
    assert data["paused"] == []
    assert "2 app updates waiting" in data["message"]


def test_installed_search_picks_status(env):
    data, _ = env.json("installed")
    assert {a["id"] for a in data["apps"]} == {"com.spotify.Client", "org.videolan.VLC"}
    assert data["apps"][0]["name"] == "Spotify"
    data, _ = env.json("search", "share")
    assert [r["id"] for r in data["results"]] == ["org.localsend.localsend_app"]
    data, _ = env.json("picks")
    ids = {p["id"]: p for p in data["picks"]}
    assert ids["com.spotify.Client"]["installed"] is True
    assert ids["com.discordapp.Discord"]["installed"] is False
    studio = data["studio"]["apps"]
    assert studio[0]["flatpak_id"] == "ai.storyteller.photocraft"
    assert all(a.get("flatpak_id") or a.get("coming_soon") for a in studio)   # no made-up ids
    data, _ = env.json("status")
    assert data["flathub"] is True and data["auto_update"] == "auto"


def test_catalog_ids_are_valid_and_unique():
    cat = orion_apps.load_catalog()
    ids = [p["id"] for p in cat["picks"]]
    assert len(ids) == len(set(ids))
    assert all(orion_apps.valid_app_id(i) for i in ids)
    for want in ("com.discordapp.Discord", "com.valvesoftware.Steam", "com.obsproject.Studio", "org.blender.Blender",
                 "com.vscodium.codium", "com.spotify.Client", "org.localsend.localsend_app", "md.obsidian.Obsidian"):
        assert want in ids
    assert all(p["category"] in cat["categories"] for p in cat["picks"])


def test_install_and_remove_record_history(env):
    data, code = env.json("install", "org.localsend.localsend_app")
    assert code == 0 and data["result"] == "installed" and data["healthy"] is True
    assert "org.localsend.localsend_app" in env.state()["apps"]
    data, code = env.json("install", "org.localsend.localsend_app")
    assert data["result"] == "already"
    data, code = env.json("remove", "org.localsend.localsend_app")
    assert code == 0 and data["result"] == "removed"
    kinds = [c["kind"] for c in env.history()]
    assert kinds == ["app_remove", "app_install"]
    assert env.history()[1]["undo"] == ["orion-apps", "remove", "org.localsend.localsend_app"]


def test_install_refuses_bad_or_unknown_ids(env):
    data, code = env.json("install", "rm -rf /")
    assert code == 1 and "isn't an app name" in data["message"]
    data, code = env.json("install", "com.example.NotOnFlathub")
    assert code == 1 and "Nothing was installed" in data["message"]
    assert not any(c[0] == "install" for c in env.state()["calls"])


# ------------------------------------------------------------------------------------------------ safe updates
def test_update_all_happy_path(env):
    data, code = env.json("update-all")
    assert code == 0
    res = {r["app"]: r for r in data["results"]}
    assert res["com.spotify.Client"]["result"] == "updated"
    assert res["com.spotify.Client"]["from_commit"] == "sp1" and res["com.spotify.Client"]["to_commit"] == "sp2"
    assert res["org.videolan.VLC"]["result"] == "updated"
    assert env.state()["apps"]["com.spotify.Client"]["commit"] == "sp2"
    assert env.state()["masked"] == []
    h = env.history()
    assert {c["kind"] for c in h} == {"app_update"}
    assert h[-1]["checks"][0]["before"] is True and h[-1]["checks"][0]["after"] is True
    assert h[-1]["undo"][-2:] == ["--commit=sp1", "com.spotify.Client"]
    assert "Updated 2 apps" in data["summary"]


def test_update_that_breaks_is_rolled_back_and_paused(env):
    env.set(broken=["sp2"])
    data, code = env.json("update-all")
    res = {r["app"]: r for r in data["results"]}
    sp = res["com.spotify.Client"]
    assert sp["result"] == "rolled_back"
    assert env.state()["apps"]["com.spotify.Client"]["commit"] == "sp1"      # old version back
    assert "com.spotify.Client" in env.state()["masked"]                      # and paused
    assert res["org.videolan.VLC"]["result"] == "updated"                     # others carry on
    assert "put the previous version back and paused its updates" in sp["message"]
    calls = env.state()["calls"]
    assert ["update", "--system", "-y", "--noninteractive", "--commit=sp1", "com.spotify.Client"] in calls
    rb = [c for c in env.history() if c["kind"] == "app_rollback"][0]
    assert rb["bad_commit"] == "sp2" and rb["to_commit"] == "sp1" and rb["paused"] is True
    # paused apps show up in `updates` and can be resumed
    data, _ = env.json("updates")
    assert data["paused"] == ["com.spotify.Client"]
    data, code = env.json("resume", "com.spotify.Client")
    assert code == 0 and env.state()["masked"] == []


def test_runtime_rolled_back_when_app_rollback_alone_does_not_help(env):
    env.set(remote_runtimes={RT: "rt2"}, broken=["rt2"])
    data, _ = env.json("update-all")
    sp = {r["app"]: r for r in data["results"]}["com.spotify.Client"]
    assert sp["result"] == "rolled_back" and sp["runtime_rolled_back"] is True
    st = env.state()
    assert st["runtimes"][RT] == "rt1"
    assert "org.freedesktop.Platform/x86_64/24.08" in st["masked"]
    assert st["apps"]["com.spotify.Client"]["commit"] == "sp1"


def test_app_that_never_started_is_not_rolled_back(env):
    env.set(broken=["sp1"])      # broken before the update: the check can't tell us anything
    data, _ = env.json("update-all")
    sp = {r["app"]: r for r in data["results"]}["com.spotify.Client"]
    assert sp["result"] == "updated" and "couldn't check it" in sp["message"]
    assert env.state()["masked"] == []


def test_failed_update_is_reported_not_rolled_back(env):
    env.set(fail_update=["com.spotify.Client"])
    data, _ = env.json("update-all")
    sp = {r["app"]: r for r in data["results"]}["com.spotify.Client"]
    assert sp["result"] == "failed" and "unchanged" in sp["message"]


def test_runtime_only_update_breaking_apps_is_undone(env):
    s = world()
    s["remote"] = {}                     # no app updates, only the runtime
    s["remote_runtimes"] = {RT: "rt2"}
    s["broken"] = ["rt2"]
    (env.path / "flatpak.json").write_text(json.dumps(s))
    data, _ = env.json("update-all")
    r = data["results"][0]
    assert r["result"] == "rolled_back" and set(r["affected"]) == {"com.spotify.Client", "org.videolan.VLC"}
    assert env.state()["runtimes"][RT] == "rt1"
    assert [c["kind"] for c in env.history()] == ["runtime_rollback"]


def test_nothing_to_do(env):
    s = world()
    s["remote"] = {}
    (env.path / "flatpak.json").write_text(json.dumps(s))
    data, _ = env.json("update-all")
    assert data["results"] == [] and data["summary"] == "Every app is up to date."


# ------------------------------------------------------------------------------------------------ weekly + config
def test_weekly_follows_config(env):
    data, _ = env.json("config", "auto-update", "off")
    assert data["auto_update"] == "off"
    data, _ = env.json("update-all", "--auto")
    assert "off" in data["message"]
    assert env.state()["apps"]["com.spotify.Client"]["commit"] == "sp1"
    env.json("config", "auto-update", "notify")
    data, _ = env.json("update-all", "--auto")
    assert len(data["updates"]) == 2
    assert env.state()["apps"]["com.spotify.Client"]["commit"] == "sp1"     # only told, nothing changed
    env.json("config", "auto-update", "auto")
    data, _ = env.json("update-all", "--auto")
    assert env.state()["apps"]["com.spotify.Client"]["commit"] == "sp2"
    assert env.history()[0]["why"] == "weekly app updates (auto)"


def test_bad_config_falls_back_to_default(env):
    Path(os.environ["ORION_APPS_CONFIG"]).write_text('{"auto_update": "sometimes"}')
    assert orion_apps.load_config()["auto_update"] == "auto"


# ------------------------------------------------------------------------------------------------ Studio bundles
def test_install_bundle_checks_checksum(env, tmp_path):
    import hashlib
    f = tmp_path / "photocraft-0.9.0-linux-x86_64.flatpak"
    f.write_bytes(b"bundle")
    data, code = env.json("install-bundle", str(f))
    assert code == 1 and "SHA256SUMS.txt" in data["message"]
    (tmp_path / "SHA256SUMS.txt").write_text(f"{'0' * 64}  {f.name}\n")
    data, code = env.json("install-bundle", str(f))
    assert code == 1 and "doesn't match" in data["message"]
    (tmp_path / "SHA256SUMS.txt").write_text(f"{hashlib.sha256(b'bundle').hexdigest()}  {f.name}\n")
    data, code = env.json("install-bundle", str(f))
    assert code == 0 and data["app"] == "ai.storyteller.photocraft"
    other = tmp_path / "something-1.0.flatpak"
    other.write_bytes(b"x")
    (tmp_path / "SHA256SUMS.txt").write_text(f"{hashlib.sha256(b'x').hexdigest()}  {other.name}\n")
    data, code = env.json("install-bundle", str(other))
    assert code == 1 and "isn't one of the Orion Studio apps" in data["message"]


def test_parser_skips_header_lines():
    rows = orion_apps._rows("Application ID\tName\nfoo.bar.Baz\tBaz\n\n", 2, "Application ID")
    assert rows == [["foo.bar.Baz", "Baz"]]
    assert orion_apps.ref_to_pattern(RT) == "org.freedesktop.Platform/x86_64/24.08"


# ------------------------------------------------------------------------------------------------ offline + progress
def test_offline_is_said_plainly_and_changes_nothing(env):
    env.set(offline=True)
    data, code = env.json("update-all")
    assert code == 1 and data["offline"] is True and "couldn't reach Flathub" in data["message"]
    assert data["results"] == [] and env.history() == []
    data, code = env.json("updates")
    assert code == 1 and "couldn't reach Flathub" in data["message"]
    data, code = env.json("install", "org.localsend.localsend_app")
    assert code == 1 and data.get("offline") is True
    data, code = env.json("update-all", "--auto")
    assert code == 1 and data["offline"] is True


def test_install_reports_progress(env):
    seen = []
    res = orion_apps.act_install(orion_apps.Flatpak(), "org.localsend.localsend_app",
                                 progress=lambda pct, line: seen.append(pct))
    assert res["ok"] and res["result"] == "installed"
    assert [p for p in seen if p is not None] == [5, 40, 100]


def test_weekly_asks_orions_permission_level(env, monkeypatch):
    """Self-running (complete plan step 4): Orion's level for app updates wins over "auto"; frozen stops it."""
    monkeypatch.setenv("ORION_SELF_DECIDE", "frozen")
    data, _ = env.json("update-all", "--auto")
    assert data.get("frozen") and env.state()["apps"]["com.spotify.Client"]["commit"] == "sp1"
    monkeypatch.setenv("ORION_SELF_DECIDE", "ask")
    data, _ = env.json("update-all", "--auto")
    assert data["mode"] == "notify" and len(data["updates"]) == 2
    assert env.state()["apps"]["com.spotify.Client"]["commit"] == "sp1"     # only told, nothing changed
    monkeypatch.setenv("ORION_SELF_DECIDE", "do")
    env.json("update-all", "--auto")
    assert env.state()["apps"]["com.spotify.Client"]["commit"] == "sp2"
