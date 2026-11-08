"""Tests for the Windows-style desktop parts (os/desktop): windows from Sway's tree, Alt+Tab order, the title-bar
menu, the "Window buttons" setting, keyboard layouts, and the shortcut overlay read from the real sway-config.
   python3 -m pytest os/desktop/tests
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
OS = HERE.parents[1]
sys.path.insert(0, str(HERE.parent))
import orion_keyboard as K  # noqa: E402
import orion_shortcuts as S  # noqa: E402
import orion_windows as W  # noqa: E402

TREE = json.loads((HERE / "samples" / "tree.json").read_text())
LST = (HERE / "samples" / "evdev.lst").read_text()


# ------------------------------------------------------------------ windows
def test_windows_lists_real_apps_not_the_hud():
    wins = W.windows(TREE)
    assert [w["id"] for w in wins] == [40, 20, 21, 22, 30]
    calc, ff, foot, settings, code = wins
    assert calc["minimised"] and calc["workspace"] is None and calc["app"] == "Calculator"
    assert ff["title"] == "Orion docs — Mozilla Firefox" and ff["workspace"] == "2"
    assert foot["focused"] and settings["app_id"] == "ai.orion.Settings"
    assert code["xwayland"] and code["app"] == "Code" and code["workspace"] == "3"
    assert W.focused(wins)["id"] == 21
    assert W.windows(None) == [] and W.windows({"type": "root"}) == []


def test_alt_tab_puts_the_previous_window_first_and_the_current_last():
    wins = W.windows(TREE)
    mru = W.remember(W.remember(W.remember([], 30), 20), 21)      # used Code, then Firefox, now the terminal
    order = [w["id"] for w in W.mru_order(wins, mru)]
    assert order[0] == 20 and order[1] == 30 and order[-1] == 21
    assert W.remember([1, 2, 3], 3) == [3, 1, 2]
    assert W.parse_mru("[3, 1, \"2\", null]") == [3, 1, 2] and W.parse_mru("junk") == []


def test_switcher_labels_round_trip():
    wins = W.windows(TREE)
    labels = [W.label(w) for w in wins]
    assert labels[0] == "Calculator  (minimised)"
    assert labels[4] == "Code  ·  main.py - Visual Studio Code  (workspace 3)"
    assert W.pick(wins, labels[1] + "\n")["id"] == 20 and W.pick(wins, "nothing") is None
    assert W.focus_command(wins[0]) == "[con_id=40] scratchpad show, floating disable, focus"
    assert W.focus_command(wins[1]) == "[con_id=20] focus"


def test_window_menu_actions():
    assert W.action_command("close", 21) == "[con_id=21] kill"
    assert W.action_command("maximise", 21) == "[con_id=21] fullscreen toggle"
    assert W.action_command("minimise", 21) == "[con_id=21] move scratchpad"
    assert W.action_command("rm -rf", 21) is None
    assert {a for a, _ in W.MENU} == {"close", "maximise", "minimise", "float", "next"}


def test_window_buttons_setting():
    on, off = W.buttons_conf(True), W.buttons_conf(False)
    assert "default_border normal 2" in on and "smart_borders off" in on
    assert "default_border pixel 2" in off and "smart_borders on" in off
    assert W.buttons_state(None) and W.buttons_state(on) and not W.buttons_state(off)
    assert W.live_border_command(False) == "[all] border pixel 2"
    cfg = (OS / "lab" / "sway-config").read_text()
    assert "default_border normal 2" in cfg      # on by default


# ------------------------------------------------------------------ keyboard
def test_keyboard_layouts_from_the_system_list():
    data = K.parse_lst(LST)
    assert ("gb", "English (UK)") in data["layouts"] and len(data["layouts"]) == 10
    assert ("us", "intl", "English (US, intl., with dead keys)") in data["variants"]
    assert K.name_of(data, "gb", "extd") == "English (UK, extended, Windows)"
    assert K.valid(data, "de") and K.valid(data, "us", "intl")
    assert not K.valid(data, "us", "nope") and not K.valid(data, "xx") and not K.valid(data, "de; exec rm")
    assert K.split("us:intl") == ("us", "intl") and K.split("gb") == ("gb", "")
    common = K.common_choices(data)
    assert common[0] == ("us", "English (US)") and ("gb", "English (UK)") in common
    assert ("us:intl", "English (US, intl., with dead keys)") in K.choices(data)


def test_keyboard_guess_conf_and_commands():
    data = K.parse_lst(LST)
    assert K.suggest("GB", data) == "gb" and K.suggest("DE") == "de" and K.suggest(None) == "us"
    assert K.suggest("NO", data) == "us"                    # not in this trimmed list: the safe default
    conf = K.render_conf("gb", "extd")
    assert K.parse_conf(conf) == ("gb", "extd")
    assert K.parse_conf(K.render_conf("de")) == ("de", "")
    assert K.parse_conf(None) is None and K.parse_conf("# nothing") is None
    assert K.sway_commands("fr") == ["input type:keyboard xkb_layout fr", 'input type:keyboard xkb_variant ""']
    inputs = [{"type": "keyboard", "name": "Virtual core keyboard", "xkb_active_layout_name": "English (US)"},
              {"type": "keyboard", "name": "AT Translated Set 2 keyboard", "xkb_active_layout_name": "German"}]
    assert K.active_from_inputs(inputs) == "German"


def test_sway_config_has_no_hard_coded_us_layout():
    cfg = (OS / "lab" / "sway-config").read_text()
    assert "xkb_layout us" not in cfg
    assert "include ~/.config/orion/sway.d/*.conf" in cfg


# ------------------------------------------------------------------ shortcut overlay
def real_shortcuts(tmp_path):
    home = tmp_path / "home"
    (home / ".config/orion/sway.d").mkdir(parents=True)
    (home / ".config/orion/sway.d/mine.conf").write_text("# my own\nbindsym $mod+b exec firefox\n"
                                                         "bindsym $mod+Shift+t \\\n    exec thunar\n")
    text = (OS / "lab" / "sway-config").read_text()
    b = S.parse(text, env={"XDG_RUNTIME_DIR": str(tmp_path / "run")}, home=str(home), base=str(OS / "lab"))
    return b, S.shortcuts(b)


def test_parses_the_real_sway_config_and_includes(tmp_path):
    b, groups = real_shortcuts(tmp_path)
    keys = {x["keys"] for x in b}
    assert "Mod4+d" in keys and "Mod1+Tab" in keys and "Mod4+b" in keys and "Mod4+Shift+t" in keys
    assert not any(x["keys"] == "Mod4" or x["command"].startswith("swaymsg") and "bar" in x["command"] for x in b)
    flat = {it["does"]: it["keys"] for g in groups for it in g["items"]}
    assert flat["Open an app"] == ["Super+D", "Ctrl+Alt+Space"]
    assert flat["Switch between windows"] == ["Alt+Tab"]
    assert flat["Close the window"][:2] == ["Super+Shift+Q", "Alt+F4"]
    assert "Right click on a title bar" in flat["Window menu: close, maximise, minimise"]
    assert flat["Show these shortcuts"][:2] == ["Super+/", "Ctrl+Alt+/"] and "Hold Super" in flat["Show these shortcuts"]
    assert flat["Orion (back to the Orion screen)"] == ["Super+O", "Ctrl+Alt+O"]
    assert flat["Go to workspace 3"] == ["Super+3", "Ctrl+Alt+3"]
    assert flat["Screenshot of the whole screen"] == ["Print Screen"]
    assert flat["Louder"] == ["Volume up key"]
    assert flat["Run firefox"] == ["Super+B"] and flat["Run thunar"] == ["Super+Shift+T"]
    assert [g["group"] for g in groups][:3] == ["Windows", "Apps and Orion", "Workspaces"]


def test_parser_variables_blocks_and_loops(tmp_path):
    (tmp_path / "a.conf").write_text("include b.conf\nbindsym $mod+1 workspace number 1\n")
    (tmp_path / "b.conf").write_text("include a.conf\nset $x exec foot\nbindsym $mod+t $x\n")
    text = ("set $mod Mod4\nmode \"resize\" {\n  bindsym Left resize shrink width 10px\n}\n"
            "bar {\n  colors {\n    background #000000\n  }\n}\n"
            "bindsym --to-code $mod+q kill\ninclude " + str(tmp_path) + "/*.conf\n")
    b = S.parse(text, env={}, home=str(tmp_path), base=str(tmp_path))
    assert [(x["keys"], x["command"]) for x in b] == [("Mod4+q", "kill"), ("Mod4+t", "exec foot"),
                                                       ("Mod4+1", "workspace number 1")]
    assert b[0]["flags"] == ["--to-code"]
    assert S.pretty_keys("Mod4+Shift+comma") == "Super+Shift+,"
    assert S.describe("exec /usr/bin/some-tool --x") == ("Run some-tool", "More")
    assert S.active_config({"config": "bindsym x kill"}, [])[0] == "bindsym x kill"
    assert S.active_config(None, [str(tmp_path / "missing")]) == (None, ".")


def test_commands_parse():
    for c in ("orion-windows", "orion-keyboard", "orion-shortcuts", "orion-check-hardware"):
        subprocess.run([sys.executable, "-c", f"import ast,sys; ast.parse(open(sys.argv[1]).read())",
                        str(OS / "lab" / "bin" / c)], check=True)
    for c in ("orion-polkit-agent", "orion-tray", "orion-gpu-env", "orion-session"):
        subprocess.run(["sh", "-n", str(OS / "lab" / "bin" / c)], check=True)
    json.loads("\n".join(ln for ln in (OS / "desktop" / "waybar" / "config.jsonc").read_text().splitlines()
                         if not ln.lstrip().startswith("//")))


def test_stock_waybar_bar_is_masked():
    # Ubuntu's waybar.service would put a second, default bar across the top of every session (found in a boot test).
    text = (Path(__file__).resolve().parents[2] / "image/os-desktop.sh").read_text()
    assert "ln -sfn /dev/null /etc/systemd/user/waybar.service" in text
    assert "graphical-session.target.wants/waybar.service" in text


def test_stock_mako_is_masked():
    # Ubuntu's mako.service took the notification name first and orion-mako.service failed (found in a boot test).
    text = (Path(__file__).resolve().parents[2] / "image/os-desktop.sh").read_text()
    assert "ln -sfn /dev/null /etc/systemd/user/mako.service" in text
