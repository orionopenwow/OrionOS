"""Orion Settings: the system layer with the fake runner (no NetworkManager, PipeWire or Bluetooth needed)."""
from __future__ import annotations

import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from orion_settings.backend import Result, Runner, System, plain_error  # noqa: E402
from orion_settings.fake import FakeRunner  # noqa: E402


def mk(tmp_path, **kw) -> tuple[System, FakeRunner]:
    r = FakeRunner(**kw)
    return System(r, home=str(tmp_path)), r


def test_wifi_password_never_on_command_line(tmp_path):
    sysm, r = mk(tmp_path)
    pw = "correct" + "-horse-" + "battery"          # built at runtime ([fake-secrets])
    ok, msg = sysm.wifi_connect("Library 5G", pw, "WPA3")
    assert ok, msg
    assert all(pw not in " ".join(c) for c in r.calls)
    assert r.secrets == [f"802-11-wireless-security.psk:{pw}\n"]
    add = next(c for c in r.calls if c[:3] == ["nmcli", "connection", "add"])
    assert add[-1] == "sae"                          # WPA3-only network
    up = next(c for c in r.calls if "passwd-file" in c)
    assert up[-1] == "{SECRET_FD}"


def test_wifi_connect_rules(tmp_path):
    sysm, r = mk(tmp_path)
    assert sysm.wifi_connect("Office", "x" * 10, "WPA2 802.1X")[0] is False
    assert sysm.wifi_connect("Neighbour Net", "short", "WPA2")[1].startswith("Wi-Fi passwords")
    assert sysm.wifi_connect("Neighbour Net", None, "WPA2")[1] == "This network needs its password."
    ok, _ = sysm.wifi_connect("Old Hotel")                    # saved: just bring it up
    assert ok and ["nmcli", "--wait", "30", "connection", "up", "id", "Old Hotel"] in r.calls
    ok, _ = sysm.wifi_connect("Cafe:Guest", None, "")          # open network
    assert ok and r.calls[-1][-1] == "Cafe:Guest"
    assert sysm.wifi_connect("bad\nname")[0] is False


def test_real_runner_passes_secret_through_a_pipe():
    r = Runner()
    res = r.run(["sh", "-c", 'cat "$1"; echo; tr "\\0" " " < /proc/$$/cmdline', "sh", "{SECRET_FD}"],
                secret="psk-" + "test-value")
    assert res.ok and res.out.splitlines()[0] == "psk-test-value"
    assert "psk-test-value" not in res.out.splitlines()[1]     # only the pipe's path is on the command line


def test_network_state(tmp_path):
    sysm, _ = mk(tmp_path)
    d = sysm.network()
    assert d["available"] and d["wifi_on"] and d["has_wifi"]
    assert d["networks"][0]["ssid"] == "Starlight Home" and d["networks"][0]["saved"]
    assert not d["airplane"]
    sysm.set_airplane(True)
    assert sysm.network()["airplane"]


def test_missing_tools_are_reported_not_crashing(tmp_path):
    sysm, _ = mk(tmp_path, missing=["nmcli", "wpctl", "bluetoothctl", "powerprofilesctl", "orion-timezone",
                                    "orion-nightlight", "orion-boot-sound", "swaymsg"])
    assert sysm.network() == {"available": False}
    assert sysm.sound()["available"] is False and sysm.sound()["boot_sound"] is None
    assert sysm.bluetooth()["available"] is False
    assert sysm.power()["profiles"] == []
    assert sysm.time() == {"available": False}
    d = sysm.displays()
    assert d["nightlight"] is None and d["outputs"] == [] and not d["orion_displays"]
    u = sysm.updates()
    assert u["apps_tool"] is False and u["apps"] is None


def test_partner_tools_when_present(tmp_path):
    sysm, _ = mk(tmp_path, extra=["orion-apps", "orion-displays", "orion-share"])
    u = sysm.updates()
    assert [a["name"] for a in u["apps"]] == ["Firefox", "LocalSend"] and u["slot"]
    assert sysm.displays()["profiles"] == ["Desk", "Laptop only"]


def test_privileged_changes_go_through_pkexec_helpers(tmp_path):
    sysm, r = mk(tmp_path)
    assert sysm.set_logind("HandleLidSwitch", "lock")[0]
    assert r.calls[-1] == ["pkexec", "/opt/orion/settings/helpers/orion-settings-power", "lid", "lock"]
    assert sysm.set_logind("HandleLidSwitch", "rm -rf")[0] is False
    assert sysm.set_logind("SomethingElse", "lock")[0] is False
    assert sysm.set_camera(True)[0]
    assert r.calls[-1] == ["pkexec", "/opt/orion/settings/helpers/orion-settings-camera", "on"]
    assert not any(c[0] == "sudo" for c in r.calls)


def test_daemon_env_edits_only_the_named_key(tmp_path):
    sysm, r = mk(tmp_path)
    conf = tmp_path / ".config" / "orion"
    conf.mkdir(parents=True)
    keep = "SOME_" + "TOKEN=" + "abc" + "123"        # whatever else lives there is kept byte for byte
    (conf / "daemon.env").write_text(f"{keep}\nORION_CAPTURE_ENABLED=1\n")
    ok, _ = sysm.set_capture(False)
    assert ok
    assert (conf / "daemon.env").read_text() == f"{keep}\nORION_CAPTURE_ENABLED=0\n"
    assert oct(os.stat(conf / "daemon.env").st_mode & 0o777) == "0o600"
    assert ["orion-ctl", "restart"] in r.calls
    assert sysm.privacy()["capture"] is False
    # the page data never carries other keys
    assert "abc123" not in repr(sysm.orion())


def test_clipboard_and_prefs(tmp_path):
    sysm, _ = mk(tmp_path)
    assert sysm.privacy()["clipboard"] is True
    sysm.set_clipboard(False)
    assert (tmp_path / ".config/orion/clipboard-history").read_text() == "off\n"
    assert sysm.privacy()["clipboard"] is False
    sysm.set_pref("timezone_lookup", False)
    assert sysm.time()["lookup_allowed"] is False


def test_appearance_solid_uses_session_env(tmp_path):
    sysm, r = mk(tmp_path)
    sysm.set_solid(True)
    assert (tmp_path / ".config/orion/session.env").read_text() == "ORION_SWAYFX=0\n"
    assert sysm.appearance()["solid"]
    sysm.set_solid(False)
    assert (tmp_path / ".config/orion/session.env").read_text() == ""
    sysm.set_animations(False)
    assert sysm.appearance()["animations"] is False


def test_scale_is_validated_and_kept(tmp_path):
    sysm, r = mk(tmp_path)
    assert sysm.set_scale("eDP-1", 1.5)[0]
    assert r.calls[-1] == ["swaymsg", "output eDP-1 scale 1.5"]
    assert sysm.set_scale("eDP-1", 1.25)[0]
    f = (tmp_path / ".config/orion/sway.d/displays.conf").read_text()
    assert f.count("output eDP-1 scale") == 1 and "scale 1.25" in f
    assert sysm.set_scale("eDP-1; exec x", 1)[0] is False
    assert sysm.set_scale("eDP-1", 9)[0] is False


def test_power_and_time(tmp_path):
    sysm, _ = mk(tmp_path)
    p = sysm.power()
    assert p["active"] == "balanced" and p["batteries"] == [{"name": "BAT0", "percent": 76,
                                                              "status": "Discharging"}]
    assert p["lid"]["HandleLidSwitchExternalPower"] == "lock"
    assert sysm.set_profile("power-saver")[0] and sysm.power()["active"] == "power-saver"
    assert sysm.set_profile("turbo")[0] is False
    assert sysm.time()["zone"] == "Europe/Lisbon"
    assert sysm.set_zone("Asia/Tokyo")[0] and sysm.time()["zone"] == "Asia/Tokyo"
    assert sysm.set_zone("../../etc/passwd")[0] is False
    assert sysm.zones()[0] == ("Europe/Lisbon", "Portugal")


def test_bluetooth(tmp_path):
    sysm, r = mk(tmp_path)
    d = sysm.bluetooth()
    assert d["available"] and d["powered"]
    assert d["devices"][0]["name"] == "Quiet Headphones" and d["devices"][0]["battery"] == 80
    assert sysm.bt_action("pair", "7C:A1:AE:00:11:22")[0]
    assert sysm.bt_action("pair", "not-a-mac")[0] is False
    assert sysm.bt_action("format", "7C:A1:AE:00:11:22")[0] is False


def test_repo_and_name_validation(tmp_path):
    sysm, r = mk(tmp_path)
    assert sysm.set_repo("https://github.com/someone/openwow")[0]
    assert "orion-ctl set-repo https://github.com/someone/openwow" in r.spawned[-1][-1]
    assert sysm.set_repo("https://x; rm -rf /")[0] is False
    sysm.set_greeting_name("Sam\nrm")
    assert r.calls[-1] == ["orion", "name", "Samrm"]


def test_plain_errors():
    assert plain_error(Result(127), "switch Wi-Fi") == "Couldn't switch Wi-Fi: the tool for it isn't installed."
    assert "permission was refused" in plain_error(Result(4, "", "Error: Not authorized to control networking."),
                                                   "switch Wi-Fi")
    assert plain_error(Result(1, "", "Error: Connection activation failed: No network with SSID 'x' found."),
                       "connect").startswith("Couldn't connect. (Connection activation failed")


def test_autonomy_is_read_only(tmp_path):
    sysm, r = mk(tmp_path)
    rows = sysm.autonomy()
    assert rows and rows[0]["task_type"] == "payout.bounty"
