"""Gravity mode, the optional Hyprland session and the desktop commands on the compositor adapter
(after-1.0 plan §3). Fake swaymsg / hyprctl programs on PATH stand in for a real session.
   python3 -m pytest os/desktop/tests
"""
from __future__ import annotations

import json
import os
import re
import stat
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
DESKTOP = HERE.parent
OS = DESKTOP.parent
REPO = OS.parent
BIN = OS / "lab" / "bin"
sys.path.insert(0, str(DESKTOP))
import orion_gravity as G  # noqa: E402


# ------------------------------------------------------------------ logic
def test_gravity_is_off_by_default_and_pauses_on_battery_or_reduced_motion():
    assert G.wanted(None) is False and G.wanted("") is False and G.wanted("off\n") is False
    assert G.wanted("on\n") is True
    assert G.effective(False, "hyprland", False, False)[0] is False
    assert G.effective(True, "hyprland", False, False)[0] is True
    on_sway, why = G.effective(True, "sway", False, False)
    assert on_sway is False and "Hyprland" in why
    assert G.effective(True, "hyprland", True, False) == (False, "Gravity mode is paused on battery. It comes back when you plug in.")
    assert G.effective(True, "hyprland", False, True)[1].startswith("Gravity mode is paused while reduced motion")


def test_battery_and_reduced_motion_detection():
    ac = ("AC", {"type": "Mains", "online": "1", "status": ""})
    ac_off = ("AC", {"type": "Mains", "online": "0", "status": ""})
    bat = ("BAT0", {"type": "Battery", "status": "Discharging", "online": ""})
    full = ("BAT0", {"type": "Battery", "status": "Full", "online": ""})
    assert G.on_battery([ac, full]) is False
    assert G.on_battery([ac_off, bat]) is True
    assert G.on_battery([bat]) is True                 # no charger listed: go by the battery
    assert G.on_battery([]) is False                   # a desktop PC
    assert G.reduced_motion("false\n", None, None)
    assert G.reduced_motion(None, "[Settings]\ngtk-enable-animations=false\n", None)
    assert G.reduced_motion(None, None, "LOGIN_MODE=auto\nREDUCED_MOTION=1\n")
    assert not G.reduced_motion("true", "[Settings]\ngtk-enable-animations=true\n", "REDUCED_MOTION=0\n")
    files = {"/s/AC/type": "Mains", "/s/AC/online": "0", "/s/BAT0/type": "Battery", "/s/BAT0/status": "Discharging"}
    sup = G.read_supplies(lambda _b: ["BAT0", "AC"], files.get, base="/s")
    assert G.on_battery(sup)


def test_session_choice_keeps_your_other_settings():
    assert G.session_choice(None) == "sway"
    text = "ORION_SWAYFX=0\n"
    t2 = G.set_session(text, "hyprland")
    assert "ORION_SWAYFX=0" in t2 and G.session_choice(t2) == "hyprland"
    t3 = G.set_session(t2, "sway")
    assert G.session_choice(t3) == "sway" and t3.count("ORION_COMPOSITOR=") == 1
    assert G.session_choice("ORION_COMPOSITOR=weird\n") == "sway"


def test_generated_files():
    assert "decoration:screen_shader = /c/g.frag" in G.gravity_conf("/c/g.frag")
    assert "screen_shader" not in G.gravity_conf(None).replace("# Orion Gravity", "")
    assert "input:kb_layout = de" in G.keyboard_conf("de", "nodeadkeys")
    bad = G.keyboard_conf("us; rm -rf /\nexec-once = x")
    lines = bad.splitlines()                          # nothing can sneak into the config as its own line
    assert len(lines) == 3 and lines[0].startswith("#") and all(ln.startswith("input:") for ln in lines[1:])
    assert ";" not in lines[1] and " " not in lines[1].split(" = ", 1)[1]
    src = (DESKTOP / "hyprland" / "gravity.frag").read_text()
    out = G.shader_for(src, 2560, 1080)
    assert "const float ASPECT = 2.3704;" in out and out.count("ASPECT = ") == 1


# ------------------------------------------------------------------ the Hyprland session files
def test_hyprland_config_keeps_the_orion_look_and_gravity_off():
    conf = (DESKTOP / "hyprland" / "hyprland.conf").read_text()
    # Gravity mode is never switched on by the config itself, only through the file orion-gravity writes
    live = [ln for ln in conf.splitlines() if not ln.strip().startswith("#")]
    assert not any("screen_shader" in ln for ln in live)
    assert "source = ~/.config/orion/hypr.d/gravity.conf" in conf
    assert "rounding = 16" in conf and "7c5cff" in conf.lower() and "dim_inactive = true" in conf
    assert "exec-once = orion-session-start" in conf and "exec-once = orion-gravity watch" in conf
    # the main shortcuts match the Sway session
    for b in ("$mod, D, exec, $menu", "ALT, Tab, exec, orion-windows switch", "$mod, slash, exec, orion-shortcuts toggle",
              "ALT, F4, killactive"):
        assert b in conf
    assert conf.count("# orion:shadow-block") == 2     # os-hyprland.sh can swap it for older Hyprland


def test_gravity_shader_contract():
    src = (DESKTOP / "hyprland" / "gravity.frag").read_text()
    code = re.sub(r"//[^\n]*", "", src)
    assert "uniform sampler2D tex;" in code and "varying vec2 v_texcoord;" in code and "gl_FragColor" in code
    assert code.count("{") == code.count("}") and code.count("(") == code.count(")")
    assert re.search(r"const float ASPECT = [0-9.]+;", code)


def test_image_keeps_sway_and_hyprland_is_opt_in():
    prov = (OS / "lab" / "provision.sh").read_text()
    assert '[ "${ORION_WITH_HYPRLAND:-0}" = 1 ]' in prov and "os-hyprland.sh" in prov
    session = (BIN / "orion-session").read_text()
    assert '"${ORION_COMPOSITOR:-sway}" = hyprland' in session and session.rstrip().endswith("exec sway $ORION_SWAY_FLAGS")
    subprocess.run(["bash", "-n", str(OS / "image" / "os-hyprland.sh")], check=True)
    subprocess.run(["sh", "-n", str(BIN / "orion-session")], check=True)
    desk = (OS / "image" / "os-desktop.sh").read_text()
    assert "orion_compositor.py" in desk and "orion-gravity" in desk


# ------------------------------------------------------------------ the commands on fake compositors
def _tool(d: Path, name: str, cases: list) -> None:
    body = ["#!/usr/bin/env python3", "import json, os, sys", "a = sys.argv[1:]",
            "open(os.environ['FAKE_LOG'], 'a').write(json.dumps([%r] + a) + '\\n')" % name,
            "cases = json.loads(%r)" % json.dumps(cases),
            "for k, v in cases:",
            "    if k in ' '.join(a):",
            "        sys.stdout.write(v); sys.exit(0)",
            "sys.stdout.write('ok')"]
    p = d / name
    p.write_text("\n".join(body) + "\n")
    p.chmod(p.stat().st_mode | stat.S_IEXEC)


def _env(tmp_path: Path, **extra) -> tuple[dict, Path]:
    b = tmp_path / "bin"
    b.mkdir(exist_ok=True)
    tree = json.loads((HERE / "samples" / "tree.json").read_text())
    _tool(b, "swaymsg", [["get_tree", json.dumps(tree)], ["get_config", json.dumps({"config": "bindsym Mod4+d exec wofi --show drun\n"})]])
    clients = [{"address": "0xabc", "class": "foot", "title": "~", "pid": 5, "mapped": True, "floating": False,
                "workspace": {"id": 2, "name": "2"}, "focusHistoryID": 0}]
    _tool(b, "hyprctl", [["clients", json.dumps(clients)], ["monitors", json.dumps([{"name": "e", "width": 1920, "height": 1200,
                          "scale": 1, "focused": True}])],
                         ["binds", json.dumps([{"modmask": 64, "key": "D", "dispatcher": "exec", "arg": "wofi --show drun"}])]])
    _tool(b, "Hyprland", [])
    log = tmp_path / "log.jsonl"
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    env = {"PATH": f"{b}{os.pathsep}{os.environ['PATH']}", "FAKE_LOG": str(log), "HOME": str(home),
           "XDG_RUNTIME_DIR": str(tmp_path), "ORION_DESKTOP_DIR": str(DESKTOP), "WAYLAND_DISPLAY": "wayland-1"}
    env.update(extra)
    return env, log


def _calls(log: Path) -> list:
    return [json.loads(x) for x in log.read_text().splitlines()] if log.exists() else []


def _run(cmd: list, env: dict) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, *cmd], env=env, capture_output=True, text=True, timeout=30)


def test_orion_windows_on_sway_and_hyprland(tmp_path):
    env, log = _env(tmp_path, SWAYSOCK="/run/sway.sock")
    r = _run([str(BIN / "orion-windows"), "list", "--json"], env)
    assert r.returncode == 0, r.stderr
    assert [w["id"] for w in json.loads(r.stdout)["windows"]] == [40, 20, 21, 22, 30]
    assert _run([str(BIN / "orion-windows"), "do", "minimise", "20"], env).returncode == 0
    assert _run([str(BIN / "orion-windows"), "focus", "40"], env).returncode == 0
    sent = [c[1:] for c in _calls(log) if c[0] == "swaymsg"]
    assert ["[con_id=20] move scratchpad"] in sent
    assert ["[con_id=40] scratchpad show, floating disable, focus"] in sent

    env, log = _env(tmp_path, HYPRLAND_INSTANCE_SIGNATURE="sig")
    log.unlink(missing_ok=True)
    r = _run([str(BIN / "orion-windows"), "list", "--json"], env)
    assert json.loads(r.stdout)["windows"][0]["app"] == "Foot"
    assert _run([str(BIN / "orion-windows"), "do", "close", str(0xabc)], env).returncode == 0
    hy = [c[1:] for c in _calls(log) if c[0] == "hyprctl"]
    assert ["dispatch", "closewindow", "address:0xabc"] in hy
    assert not any(c[0] == "swaymsg" for c in _calls(log))


def test_orion_shortcuts_reads_hyprland_binds(tmp_path):
    env, _log = _env(tmp_path, HYPRLAND_INSTANCE_SIGNATURE="sig")
    r = _run([str(BIN / "orion-shortcuts"), "list", "--json"], env)
    assert r.returncode == 0, r.stderr
    items = [i for g in json.loads(r.stdout)["groups"] for i in g["items"]]
    assert any("Super+D" in i["keys"] for i in items)
    env, _log = _env(tmp_path, SWAYSOCK="/run/sway.sock")
    r = _run([str(BIN / "orion-shortcuts"), "list", "--json"], env)
    items = [i for g in json.loads(r.stdout)["groups"] for i in g["items"]]
    assert any("Super+D" in i["keys"] for i in items)


def test_orion_gravity_command(tmp_path):
    shader = DESKTOP / "hyprland" / "gravity.frag"
    env, log = _env(tmp_path, HYPRLAND_INSTANCE_SIGNATURE="sig", ORION_GRAVITY_SHADER=str(shader))
    home = Path(env["HOME"])
    st = json.loads(_run([str(BIN / "orion-gravity"), "status", "--json"], env).stdout)
    assert st["wanted"] is False and st["active"] is False and st["session"] == "sway"   # off by default
    r = _run([str(BIN / "orion-gravity"), "on"], env)
    assert r.returncode == 0, r.stderr
    hy = [c[1:] for c in _calls(log) if c[0] == "hyprctl"]
    battery_or_motion = json.loads(_run([str(BIN / "orion-gravity"), "status", "--json"], env).stdout)
    conf = (home / ".config/orion/hypr.d/gravity.conf").read_text()
    if battery_or_motion["active"]:
        cached = home / ".cache/orion/gravity.frag"
        assert ["keyword", "decoration:screen_shader", str(cached)] in hy
        assert "const float ASPECT = 1.6000;" in cached.read_text()       # 1920x1200 from the fake monitor
        assert f"decoration:screen_shader = {cached}" in conf
    else:                                                                 # this test machine is on battery
        assert ["keyword", "decoration:screen_shader", "[[EMPTY]]"] in hy
    _run([str(BIN / "orion-gravity"), "off"], env)
    assert ["keyword", "decoration:screen_shader", "[[EMPTY]]"] in [c[1:] for c in _calls(log) if c[0] == "hyprctl"]
    assert "screen_shader =" not in (home / ".config/orion/hypr.d/gravity.conf").read_text()
    # choosing the session for the next login (Hyprland is "installed": a fake Hyprland is on PATH)
    (home / ".config/orion/session.env").write_text("ORION_SWAYFX=1\n")
    assert _run([str(BIN / "orion-gravity"), "session", "hyprland"], env).returncode == 0
    text = (home / ".config/orion/session.env").read_text()
    assert "ORION_SWAYFX=1" in text and "ORION_COMPOSITOR=hyprland" in text
    assert _run([str(BIN / "orion-gravity"), "session"], env).stdout.strip() == "hyprland"
    # prepare (before Hyprland starts) writes the keyboard and your own file
    env2 = dict(env, XKB_DEFAULT_LAYOUT="fr")
    assert _run([str(BIN / "orion-gravity"), "prepare"], env2).returncode == 0
    assert "input:kb_layout = fr" in (home / ".config/orion/hypr.d/keyboard.conf").read_text()
    assert (home / ".config/orion/hypr.d/user.conf").exists()


def test_orion_gravity_refuses_hyprland_when_not_installed(tmp_path):
    env, _log = _env(tmp_path)
    (tmp_path / "bin" / "Hyprland").unlink()
    env["PATH"] = f"{tmp_path / 'bin'}{os.pathsep}/usr/bin{os.pathsep}/bin"
    r = _run([str(BIN / "orion-gravity"), "session", "hyprland"], env)
    assert r.returncode == 1 and "isn't installed" in r.stderr
