"""Settings > Accessibility, Languages & typing, Users (guest, add a person) and battery care: the command side."""
from __future__ import annotations

import json
import shlex
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from orion_settings import everyone as EV  # noqa: E402
from orion_settings.backend import Result, System  # noqa: E402
from orion_settings.fake import FakeRunner  # noqa: E402

TOOLS = ["orion-access", "orion-typing", "orion-power", "orion-people", "orca", "wvkbd-mobintl"]


class Runner(FakeRunner):
    def _answer(self, a):
        if a[0] == "orion-access" and a[1] == "status":
            return Result(0, json.dumps({"screen_reader": False, "contrast": True, "text_scale": 1.5,
                                         "screen_scale": None, "hud_zoom": 1.0, "keyboard": False,
                                         "keyboard_touch": True}))
        if a[0] == "orion-typing" and a[1] == "status":
            return Result(0, json.dumps({"languages": [{"id": "keyboard-us", "name": "English (US)"}],
                                         "enabled": False, "fcitx5": True, "missing": [], "choices": []}))
        if a[0] == "orion-power" and a[1] == "status":
            return Result(0, json.dumps({"present": True, "percent": 50, "hibernate_possible": False,
                                         "hibernate_reason": "Off on the USB stick"}))
        if a[0] == "orion-power":
            return Result(0, "Saved.\n")
        if a[0] == "orion-people":
            return Result(0, json.dumps({"me": "orion", "is_guest": False, "people": ["orion"],
                                         "guest_enabled": True}))
        if a[0] in ("orion-access", "orion-typing"):
            return Result(0, "High contrast on.\n")
        return super()._answer(a)


def mk(tmp_path, **kw):
    r = Runner(extra=TOOLS, **kw)
    return System(r, home=str(tmp_path)), r


def test_access_read_and_set(tmp_path):
    s, r = mk(tmp_path)
    d = EV.access(s)
    assert d["available"] and d["contrast"] and d["text_scale"] == 1.5 and d["orca"] and d["animations"]
    assert EV.set_access(s, "contrast", "on") == (True, "High contrast on.")
    assert ["orion-access", "contrast", "on"] in r.calls
    assert EV.set_access(s, "text-size", "1.25")[0]
    assert not EV.set_access(s, "rm", "on")[0]
    assert not EV.set_access(s, "contrast", "on; reboot")[0]


def test_access_missing_tool(tmp_path):
    r = Runner(missing=["orion-access"])
    s = System(r, home=str(tmp_path))
    assert EV.access(s)["available"] is False


def test_typing(tmp_path):
    s, r = mk(tmp_path)
    assert EV.typing(s)["languages"][0]["id"] == "keyboard-us"
    assert EV.typing_change(s, "add", "mozc")[0] and ["orion-typing", "add", "mozc"] in r.calls
    assert not EV.typing_change(s, "add", "../../x")[0]
    assert not EV.typing_change(s, "purge", "mozc")[0]


def test_hibernate_switch_sets_the_lid(tmp_path):
    s, r = mk(tmp_path)
    assert EV.power(s)["care"]["hibernate_reason"].startswith("Off on the USB")
    ok, msg = EV.set_power_pref(s, "hibernate", True)
    assert ok, msg
    helper = next(c for c in r.calls if c[0] == "pkexec")
    assert helper[-2:] == ["lid", "suspend-then-hibernate"]
    assert EV.set_power_pref(s, "auto-saver", False)[0]
    assert not EV.set_power_pref(s, "reboot", True)[0]


def test_people_commands_are_quoted_and_go_through_orion_admin():
    cmd = EV.add_person_command("sam", "Sam O'Neil; rm -rf /")
    assert cmd and cmd.startswith("su -P - orion-admin -c ")
    inner = shlex.split(cmd)[-1]
    parts = shlex.split(inner)
    assert parts[:3] == ["sudo", EV.PEOPLE_ADMIN, "add"] and parts[3] == "sam"
    assert parts[4] == "Sam O'Neil; rm -rf /"            # one argument, never run as a command
    assert EV.add_person_command("Sam", "x") is None and EV.add_person_command("orion-x", "") is None
    assert EV.add_person_command("guest", "") is None
    assert shlex.split(shlex.split(EV.guest_command(True))[-1])[-2:] == ["guest", "on"]


def test_start_guest_logs_out(tmp_path):
    s, r = mk(tmp_path)
    ok, _ = EV.start_guest(s)
    assert ok and ["pkexec", EV.GUEST_START] in r.calls and ["swaymsg", "exit"] in r.spawned
