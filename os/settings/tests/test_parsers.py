"""Orion Settings: the output parsers, fed with sample outputs of the real tools (tests/samples)."""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from orion_settings import parsers as P  # noqa: E402


def s(name: str) -> str:
    return (HERE / "samples" / name).read_text(encoding="utf-8")


# ------------------------------------------------------------------ nmcli
def test_nm_split_escapes():
    assert P.nm_split(r"*:Cafe\:Guest:57:") == ["*", "Cafe:Guest", "57", ""]
    assert P.nm_split(r"a\\b:c") == ["a\\b", "c"]


def test_wifi_list_merges_hides_and_sorts():
    nets = P.parse_nm_wifi_list(s("nmcli-wifi-list.txt"))
    names = [n["ssid"] for n in nets]
    assert names[0] == "Starlight Home"                     # the one in use first
    assert "" not in names                                   # hidden network left out
    assert names.count("Starlight Home") == 1                # two access points, one entry
    home = nets[0]
    assert home["in_use"] and home["signal"] == 82 and home["security"] == "WPA2"
    cafe = next(n for n in nets if n["ssid"] == "Cafe:Guest")
    assert cafe["security"] == "" and not cafe["enterprise"]
    office = next(n for n in nets if n["ssid"] == "Office")
    assert office["enterprise"]
    lib = next(n for n in nets if n["ssid"] == "Library 5G")
    assert lib["sae"]
    rest = [n["signal"] for n in nets[1:]]
    assert rest == sorted(rest, reverse=True)


def test_connections_and_devices():
    cons = P.parse_nm_connections(s("nmcli-connections.txt"))
    assert {"name": "Old Hotel", "type": "802-11-wireless", "device": ""} in cons
    devs = P.parse_nm_devices(s("nmcli-devices.txt"))
    assert [d["device"] for d in devs] == ["wlp2s0", "enp3s0", "p2p-dev-wlp2s0"]   # no loopback
    assert devs[0]["connection"] == "Starlight Home"


def test_wifi_words():
    assert [P.wifi_bars(x) for x in (90, 50, 30, 5)] == ["Strong", "Good", "Weak", "Very weak"]


# ------------------------------------------------------------------ wpctl
def test_wpctl_status():
    st = P.parse_wpctl_status(s("wpctl-status-0.4.txt"))
    assert [n["id"] for n in st["sinks"]] == [48, 55]
    assert st["sinks"][0] == {"id": 48, "name": "Built-in Audio Analog Stereo", "default": True, "volume": 0.55,
                              "muted": False}
    assert st["sinks"][1]["muted"] and not st["sinks"][1]["default"]
    assert [n["name"] for n in st["sources"]] == ["Built-in Audio Analog Stereo", "USB Headset Mono"]
    # the Video section's sources (camera) are not sound inputs
    assert all("Camera" not in n["name"] for n in st["sources"])


def test_wpctl_status_dummy():
    st = P.parse_wpctl_status(s("wpctl-status-empty.txt"))
    assert st["sinks"][0]["name"] == "Dummy Output" and st["sources"] == []


def test_wpctl_volume():
    assert P.parse_wpctl_volume(s("wpctl-volume.txt")) == (0.55, False)
    assert P.parse_wpctl_volume(s("wpctl-volume-muted.txt")) == (0.80, True)
    assert P.parse_wpctl_volume("") == (None, False)


# ------------------------------------------------------------------ bluetoothctl
def test_bt_show():
    st = P.parse_bt_show(s("bt-show.txt"))
    assert st == {"address": "5C:F3:70:8A:21:0C", "name": "orionos", "powered": True, "discovering": False,
                  "pairable": True}
    assert P.parse_bt_show(s("bt-show-none.txt")) is None
    assert P.parse_bt_show("") is None


def test_bt_devices_and_info():
    devs = P.parse_bt_devices(s("bt-devices.txt"))
    assert [d["name"] for d in devs] == ["Quiet Headphones", "Desk Keyboard", "Living Room Speaker",
                                         "11-22-33-44-55-66"]
    info = P.parse_bt_info(s("bt-info.txt"))
    assert info["paired"] and info["connected"] and info["trusted"] and info["battery"] == 80
    assert P.bt_kind(info["icon"]) == "Headset"
    assert P.bt_kind("something-new") == "Device"
    assert P.MAC_RE.match("38:18:4C:12:9A:7E") and not P.MAC_RE.match("38:18:4C:12:9A:7E; rm")


# ------------------------------------------------------------------ power
def test_power_profiles():
    assert P.parse_power_profiles(s("powerprofiles-list.txt")) == (["performance", "balanced", "power-saver"],
                                                                   "balanced")
    assert P.parse_power_profiles(s("powerprofiles-list-noperf.txt")) == (["balanced", "power-saver"],
                                                                          "power-saver")
    assert P.parse_power_profiles("") == ([], None)


def test_logind_dropin():
    assert P.parse_logind_dropin(s("logind-dropin.conf")) == {
        "HandleLidSwitch": "suspend", "HandleLidSwitchExternalPower": "lock", "HandlePowerKey": "poweroff"}
    assert P.parse_logind_dropin("[Other]\nHandleLidSwitch=ignore\n") == {}


# ------------------------------------------------------------------ sway, Orion tools
def test_sway_outputs():
    outs = P.parse_sway_outputs(s("sway-outputs.json"))
    assert outs[0]["name"] == "eDP-1" and outs[0]["scale"] == 1.25 and outs[0]["refresh"] == 165
    assert outs[1]["label"] == "Dell Inc. DELL U2720Q" and outs[1]["width"] == 3840
    assert P.parse_sway_outputs("not json") == []


def test_sway_outputs_headless_capture():
    """Captured from the test chroot's headless Sway (swaymsg -t get_outputs -r)."""
    p = HERE / "samples" / "sway-outputs-headless.json"
    if p.exists():
        outs = P.parse_sway_outputs(p.read_text())
        assert outs and outs[0]["name"].startswith("HEADLESS")


def test_timezone_status():
    assert P.parse_timezone_status(s("timezone-status.txt")) == {
        "zone": "Europe/Lisbon", "clock": "21:14 WEST", "ntp": True, "synced": True, "rtc": "UTC"}


def test_zone_lines():
    z = P.parse_zone_lines("Europe/London  ·  United Kingdom\nUTC  ·  Coordinated Universal Time\nEtc/GMT+5\n")
    assert z == [("Europe/London", "United Kingdom"), ("UTC", "Coordinated Universal Time"), ("Etc/GMT+5", "")]


def test_nightlight_and_login():
    assert P.parse_nightlight("Night light: on (-t 4000 -l 38.72 -L -9.13)") == "on"
    assert P.parse_nightlight("Night light: off") == "off"
    assert P.parse_nightlight("Night light: not available here. This screen can't…") == "unavailable"
    assert P.parse_nightlight("Night light: on, not running yet") == "waiting"
    st = P.parse_login_status("Login: the starry login screen asks for your password.\n"
                              "Login animation: a simple fade (reduced motion).\n")
    assert st == {"password": True, "reduced_motion": True}
    assert P.parse_login_status("Login: straight into Orion (auto-login).\n") == {"password": False,
                                                                                  "reduced_motion": False}


def test_env_file_roundtrip():
    text = "# mine\nexport FOO=1\nCLOUD_PROVIDER='gemini'\nOTHER=x\n"
    assert P.parse_env_file(text) == {"FOO": "1", "CLOUD_PROVIDER": "gemini", "OTHER": "x"}
    new = P.set_env_line(text, "CLOUD_PROVIDER", "anthropic")
    assert new == "# mine\nexport FOO=1\nCLOUD_PROVIDER=anthropic\nOTHER=x\n"
    assert P.set_env_line(new, "CLOUD_PROVIDER", None) == "# mine\nexport FOO=1\nOTHER=x\n"
    assert P.set_env_line("", "ORION_SWAYFX", "0") == "ORION_SWAYFX=0\n"
    for bad in (("bad key", "1"), ("OK", "two\nlines")):
        try:
            P.set_env_line("", *bad)
        except ValueError:
            pass
        else:
            raise AssertionError(bad)
