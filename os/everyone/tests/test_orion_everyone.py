"""Everyone can use it: the pure logic (no Sway, fcitx5, battery or root needed)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

import orion_everyone as E  # noqa: E402

GB = 1024 * 1024  # KiB


# ------------------------------------------------------------------ battery
def run_steps(levels, discharging=True, t0=1000.0, gap=60.0):
    st, out = {}, []
    for n, pct in enumerate(levels):
        acts, st = E.battery_step(pct, discharging, st, t0 + n * gap)
        out.append(acts)
    return out, st


def test_battery_warns_once_at_15_and_7_then_sleeps_at_4():
    out, _ = run_steps([40, 16, 15, 14, 12, 8, 7, 6, 5, 4])
    flat = [a for acts in out for a in acts]
    notes = [a for a in flat if a[0] == "notify"]
    assert [a[1] for a in notes] == ["normal", "critical", "critical"]
    assert "15%" in notes[0][2] and "7%" in notes[1][2] and "4%" in notes[2][2]
    assert flat[-1] == ("sleep",)
    assert sum(1 for a in flat if a == ("sleep",)) == 1


def test_battery_plugging_in_rearms_and_never_sleeps():
    _, st = run_steps([15, 7])
    assert set(st["warned"]) == {E.WARN, E.CRITICAL}
    acts, st = E.battery_step(3, False, st, 5000)        # charging: nothing happens, warnings re-armed
    assert acts == [] and st["warned"] == []
    acts, _ = E.battery_step(14, True, st, 5060)
    assert acts and acts[0][1] == "normal"


def test_battery_low_at_start_warns_critical_only_once():
    out, _ = run_steps([6, 6, 5])
    flat = [a for acts in out for a in acts]
    assert len(flat) == 1 and flat[0][1] == "critical"


def test_battery_grace_after_wake_and_after_sleep():
    st = {}
    acts, st = E.battery_step(4, True, st, 10_000)
    assert ("sleep",) in acts
    # woke 2 hours later, still 4%: a grace period to plug in, not straight back to sleep
    acts, st = E.battery_step(4, True, st, 10_000 + 7200)
    assert ("sleep",) not in acts
    acts, st = E.battery_step(4, True, st, 10_000 + 7200 + 60)
    assert ("sleep",) not in acts
    t, slept = 10_000 + 7200 + 60, False
    while not slept and t < 10_000 + 7200 + E.SLEEP_GRACE + 120:
        t += 60
        acts, st = E.battery_step(3, True, st, t)
        slept = ("sleep",) in acts
    assert slept and t - (10_000 + 7200) >= E.SLEEP_GRACE


def test_battery_none_is_quiet():
    assert E.battery_step(None, True, {}, 1)[0] == []


def test_read_batteries(tmp_path):
    ps = tmp_path / "sys/class/power_supply"
    for name, files in {"BAT0": {"type": "Battery", "capacity": "42", "status": "Discharging"},
                        "AC": {"type": "Mains", "online": "0"},
                        "hidpp_battery_0": {"type": "Battery", "scope": "Device", "capacity": "5",
                                            "status": "Discharging"}}.items():
        (ps / name).mkdir(parents=True)
        for k, v in files.items():
            (ps / name / k).write_text(v + "\n")
    b = E.read_batteries(str(tmp_path))
    assert b["present"] and b["percent"] == 42 and b["discharging"] and not b["on_ac"]
    assert E.read_batteries(str(tmp_path / "none"))["present"] is False


# ------------------------------------------------------------------ power modes
def test_profile_follows_battery_and_comes_back():
    st = {}
    want, st = E.profile_step(False, 60, "performance", True, st)
    assert want == "balanced"
    want, st = E.profile_step(False, 50, "balanced", True, st)
    assert want is None
    want, st = E.profile_step(False, 20, "balanced", True, st)
    assert want == "power-saver"
    want, st = E.profile_step(False, 15, "performance", True, st)    # you picked it yourself: left alone
    assert want is None
    want, st = E.profile_step(True, 15, "power-saver", True, st)
    assert want == "performance"
    assert E.profile_step(True, 90, "performance", True, st)[0] is None


def test_profile_off_or_unavailable():
    assert E.profile_step(False, 5, "balanced", False, {})[0] is None
    assert E.profile_step(False, 5, None, True, {})[0] is None


# ------------------------------------------------------------------ hibernation
MEM = "MemTotal:       16000000 kB\nMemFree: 1 kB\n"
SWAPS_BIG = "Filename Type Size Used Priority\n/swapfile file 17000000 0 -2\n"
SWAPS_SMALL = "Filename Type Size Used Priority\n/swap.img file 4194300 0 -2\n"


def test_hibernate_check():
    ok, why = E.hibernate_check(MEM, SWAPS_BIG, "freeze mem disk", "[none] integrity", "ro resume=UUID=x", False,
                                "baremetal")
    assert ok, why
    assert not E.hibernate_check(MEM, SWAPS_SMALL, "mem disk", "", "resume=x", False, "baremetal")[0]
    assert "16 GB" in E.hibernate_check(MEM, SWAPS_SMALL, "mem disk", "", "resume=x", False, "baremetal")[1]
    assert "USB stick" in E.hibernate_check(MEM, SWAPS_BIG, "mem disk", "", "resume=x", False, "image")[1]
    assert "USB stick" in E.hibernate_check(MEM, SWAPS_BIG, "disk", "", "orion.slot=a", True, "baremetal")[1]
    assert "Secure Boot" in E.hibernate_check(MEM, SWAPS_BIG, "mem disk", "none [integrity]", "resume=x", True,
                                              "baremetal")[1]
    assert "resume" in E.hibernate_check(MEM, SWAPS_BIG, "mem disk", "", "quiet", False, "baremetal")[1]
    assert E.hibernate_check(MEM, SWAPS_BIG, "mem disk", "", "quiet", True, "baremetal")[0]
    assert not E.hibernate_check(MEM, SWAPS_BIG, "freeze mem", "", "resume=x", True, "baremetal")[0]
    assert not E.hibernate_check(MEM, SWAPS_BIG, "mem disk", "", "resume=x", True, "vm")[0]


# ------------------------------------------------------------------ accessibility writers
def test_access_clean_and_defaults():
    s = E.clean_access({"contrast": "yes", "text_scale": 1.6, "screen_scale": "off", "hud_zoom": 9, "x": 1})
    assert s["contrast"] is False and s["text_scale"] == 1.5 and s["screen_scale"] is None and s["hud_zoom"] == 3.0
    assert "x" not in s
    assert E.clean_access({"screen_scale": 1.3})["screen_scale"] == 1.25


def test_access_save_load(tmp_path):
    f = tmp_path / "access.json"
    E.save_access({"contrast": True, "text_scale": 2}, f)
    assert E.load_access(f)["contrast"] is True and json.loads(f.read_text())["text_scale"] == 2.0


def test_sway_and_foot_writers():
    s = E.clean_access({"contrast": True, "text_scale": 1.5, "screen_scale": 1.25})
    sway = E.render_sway_access(s)
    assert "default_border pixel 4" in sway and "#FFD400" in sway
    assert "output * scale 1.25" in sway and "xcursor_theme Orion 48" in sway
    normal = E.render_sway_access(E.clean_access({}))
    assert "client.focused          #7C5CFF" in normal and "output" not in normal
    assert "xcursor_theme Orion 24" in normal
    foot = E.render_foot_access(s)
    assert foot.splitlines()[1] == "[main]" and "size=15.75" in foot and "background=000000" in foot
    assert "[colors]" not in E.render_foot_access(E.clean_access({}))


def test_sway_colours_match_sway_config():
    cfg = (HERE.parents[1] / "lab" / "sway-config").read_text()
    for line in E.SWAY_NORMAL[2:]:
        assert line in cfg, line


def test_gtk_theme_and_gsettings():
    assert E.gtk_theme_for(True, True, False) == "Orion-Glass-HighContrast"
    assert E.gtk_theme_for(False, True, False) == "Orion-Glass"
    assert E.gtk_theme_for(False, True, True) == "Orion-Glass-Solid"
    assert E.gtk_theme_for(False, False, False) == "Orion-Glass-Solid"
    g = E.gsettings_for(E.clean_access({"text_scale": 1.25, "screen_reader": True}), "Orion-Glass")
    assert ["gsettings", "set", "org.gnome.desktop.interface", "text-scaling-factor", "1.25"] in g
    assert ["gsettings", "set", "org.gnome.desktop.a11y.applications", "screen-reader-enabled", "true"] in g
    theme_dir = HERE.parents[1] / "theme" / "gtk" / "Orion-Glass-HighContrast"
    assert (theme_dir / "gtk-3.0" / "gtk.css").exists() and (theme_dir / "gtk-4.0" / "gtk.css").exists()


def test_env_lines_only_touch_named_keys():
    text = "ORION_SWAYFX=0\nexport QT_ACCESSIBILITY=0\n# note\n"
    new = E.set_env_lines(text, E.session_env_for_reader(True))
    assert "ORION_SWAYFX=0" in new and "# note" in new and "QT_ACCESSIBILITY=1" in new
    assert "export QT_ACCESSIBILITY=0" not in new
    off = E.set_env_lines(new, E.session_env_for_reader(False))
    assert "QT_ACCESSIBILITY" not in off and "ORION_SWAYFX=0" in off


def test_zoom_steps():
    assert E.next_zoom(1.0, 1.0, "in") == 1.25
    assert E.next_zoom(1.0, 1.0, "out") == 1.0          # never smaller than the screen's own size
    assert E.next_zoom(4.0, 1.0, "in") == 4.0
    assert E.next_zoom(2.5, 2.0, "reset") == 2.0
    assert E.next_zoom(2.0, 2.0, "in") == 2.25


def test_touch_detection():
    assert E.has_touch([{"type": "keyboard"}, {"type": "touch"}])
    assert not E.has_touch([{"type": "pointer"}]) and not E.has_touch(None)


# ------------------------------------------------------------------ typing
def test_languages_and_fcitx_profile():
    assert E.clean_languages(None, "de") == ["keyboard-de"]
    assert E.clean_languages(["bogus", "keyboard-us", "keyboard-us", "mozc"]) == ["keyboard-us", "mozc"]
    assert not E.typing_enabled(["keyboard-us"]) and E.typing_enabled(["keyboard-us", "mozc"])
    prof = E.render_fcitx_profile(["keyboard-gb", "pinyin"])
    assert "Default Layout=gb" in prof and "DefaultIM=pinyin" in prof
    assert "[Groups/0/Items/1]\n# Name\nName=pinyin" in prof
    assert E.missing_engines(["keyboard-us", "mozc", "pinyin"], ["fcitx5-mozc"]) == ["fcitx5-chinese-addons"]
    assert E.default_layout("input * xkb_layout fr\n") == "fr" and E.default_layout("") == "us"


def test_emoji_list():
    em = dict(E.emoji_list())
    assert em["\U0001F600"] == "grinning face"
    assert "\U0001F3FB" not in em                       # skin tone swatch, not an emoji to pick
    assert len(em) > 900
    assert E.emoji_from_line("\U0001F680  rocket\n") == "\U0001F680"


# ------------------------------------------------------------------ people
def test_logins():
    assert E.check_login("sam")[0]
    assert not E.check_login("Sam")[0] and not E.check_login("s")[0]
    assert not E.check_login("guest")[0] and not E.check_login("orion-x")[0]
    assert E.check_login("sam", ["sam"])[1] == "Someone already has that name."
    assert E.login_from_name("Zoë Smith") == "zoe" and E.login_from_name("1") == ""
