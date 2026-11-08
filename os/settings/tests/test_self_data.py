"""Settings > permission levels and Settings > Updates (self-running): the data side (no GTK needed)."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from orion_settings import self_data as D  # noqa: E402


class Stub:
    def __init__(self, answers=None, files=None):
        self.answers = answers or {}
        self.files = files or {}
        self.posts = []

    def http(self, method, url, body=None, timeout=4):
        if method == "POST":
            self.posts.append((url.replace(D.API, ""), body))
        return self.answers.get((method, url.replace(D.API, "")))

    def read(self, path):
        return self.files.get(path)


def test_levels_live_hide_the_core_and_fall_back_to_defaults():
    r = Stub({("GET", "/self/levels"): {"frozen": True, "levels": [
        {"kind": "app_update", "label": "Apps", "level": 3, "max": 3, "raisable": True, "shown": True},
        {"kind": "unknown_fix", "label": "x", "level": 1, "max": 1, "shown": False},
        {"kind": "protected_core", "label": "Core", "level": 1, "max": 1, "shown": True}]}})
    lv = D.levels(r)
    assert lv["live"] and lv["frozen"] and [x["kind"] for x in lv["rows"]] == ["app_update"]
    off = D.levels(Stub())
    assert not off["live"] and {x["kind"]: x["level"] for x in off["rows"]}["security_update"] == 3
    assert not {x["kind"]: x["raisable"] for x in off["rows"]}["daemon_code"]


def test_set_level_and_admin_command():
    r = Stub({("POST", "/self/levels"): {"ok": True, "pending": True, "message": "Asked your phone."}})
    assert D.set_level(r, "app_update", 3) == (True, "Asked your phone.")
    assert r.posts == [("/self/levels", {"kind": "app_update", "level": 3, "by": "Settings"})]
    assert D.set_level(Stub(), "app_update", 1)[0] is False
    cmd = D.admin_level_command("app_update", 3)
    assert "orion-admin" in cmd and "orion-self-helper level app_update 3" in cmd
    with pytest.raises(ValueError):
        D.admin_level_command("x; rm -rf /", 2)
    with pytest.raises(ValueError):
        D.channel_command("nightly")
    assert "channel beta" in D.channel_command("beta")


def test_updates_page_data():
    r = Stub({("GET", "/self/updates"): {"firmware": [{"device": "BIOS"}], "security_waiting": 2, "channel": {
        "channel": "beta", "vm_test": False, "offer": {"version": "1.2", "passed": True, "vm_ran": False,
                                                        "vm_skipped": "no KVM access"}}}})
    u = D.updates(r)
    assert u["live"] and u["channel"] == "beta" and u["vm_test"] is False and u["security_waiting"] == 2
    assert "VM run skipped: no KVM access" in D.offer_words(u["offer"])
    assert "held back" in D.offer_words({"version": "1.3", "passed": False})
    off = D.updates(Stub(files={"/etc/orion/channel": "mine\n"}))
    assert not off["live"] and off["channel"] == "mine"
    assert D.set_vm_test(Stub({("POST", "/self/updates/settings"): {}}), True)[0]


def test_check_now_words():
    def r(ans):
        return Stub({("POST", "/self/updates/check"): ans})
    assert "frozen" in D.check_now(r({"skipped": "frozen"}))[1]
    assert D.check_now(r({"newer": False, "current": "1.0"})) == (True, "Up to date (1.0).")
    assert "passed" in D.check_now(r({"newer": True, "offered": True, "latest": "1.1"}))[1]
    assert "didn't pass" in D.check_now(r({"newer": True, "offered": False, "latest": "1.1"}))[1]
    assert D.check_now(Stub())[0] is False
