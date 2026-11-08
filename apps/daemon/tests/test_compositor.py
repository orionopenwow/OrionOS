"""Compositor adapter (compositor.py): Sway and Hyprland backends driven through fake swaymsg / hyprctl / wlrctl
programs on PATH, so the real commands Orion would run are checked without a desktop session."""
import json
import os
import socket
import stat
import sys
import threading

import pytest

import compositor as C
import linux_desktop as ld

pytestmark = pytest.mark.skipif(sys.platform.startswith("win"), reason="fake tools are shell scripts")

SWAY_OUTPUTS = [{"name": "eDP-1", "active": True, "focused": True, "rect": {"x": 0, "y": 0, "width": 1280, "height": 800},
                 "scale": 1.5}, {"name": "HDMI-A-1", "active": False, "focused": False, "rect": {}}]
SWAY_TREE = {"type": "root", "nodes": [{"type": "output", "nodes": [{"type": "workspace", "name": "1", "nodes": [
    {"type": "con", "id": 7, "app_id": "foot", "name": "~", "pid": 11, "focused": True}]}]}]}
HYPR_MONITORS = [{"name": "eDP-1", "width": 2560, "height": 1600, "x": 0, "y": 0, "scale": 2.0, "focused": True,
                  "disabled": False}]
HYPR_CLIENTS = [
    {"address": "0x55d0c1a2b3c0", "class": "firefox", "title": "Docs", "pid": 21, "mapped": True, "floating": False,
     "workspace": {"id": 2, "name": "2"}, "focusHistoryID": 0, "xwayland": False},
    {"address": "0x55d0c1a2b400", "class": "org.gnome.Calculator", "title": "Calculator", "pid": 22, "mapped": True,
     "floating": True, "workspace": {"id": -98, "name": "special:minimized"}, "focusHistoryID": 2, "xwayland": False},
    {"address": "0x55d0c1a2b500", "class": "ai.orion.hud", "title": "Orion", "pid": 23, "mapped": True,
     "floating": False, "workspace": {"id": 1, "name": "1"}, "focusHistoryID": 1, "xwayland": False},
    {"address": "0x55d0c1a2b600", "class": "Code", "title": "main.py", "pid": 24, "mapped": True, "floating": False,
     "workspace": {"id": 3, "name": "3"}, "focusHistoryID": 3, "xwayland": True},
]
HYPR_BINDS = [{"modmask": 64, "key": "D", "dispatcher": "exec", "arg": "wofi --show drun", "mouse": False},
              {"modmask": 65, "key": "Q", "dispatcher": "killactive", "arg": "", "mouse": False},
              {"modmask": 64, "key": "mouse:272", "dispatcher": "movewindow", "arg": "", "mouse": True}]


def _tool(path, name, cases):
    """A fake CLI: logs its argv (one JSON list per line) and prints the first case whose key is in the args."""
    body = ["#!/usr/bin/env python3", "import json, os, sys",
            "a = sys.argv[1:]",
            "open(os.environ['FAKE_LOG'], 'a').write(json.dumps([%r] + a) + '\\n')" % name,
            "cases = json.loads(%r)" % json.dumps(cases),
            "for k, v in cases:",
            "    if k in ' '.join(a):",
            "        sys.stdout.write(v); sys.exit(0)",
            "sys.stdout.write('ok')"]
    p = path / name
    p.write_text("\n".join(body) + "\n")
    p.chmod(p.stat().st_mode | stat.S_IEXEC)


@pytest.fixture
def fake(tmp_path, monkeypatch):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    log = tmp_path / "log.jsonl"
    _tool(bindir, "swaymsg", [["get_outputs", json.dumps(SWAY_OUTPUTS)], ["get_tree", json.dumps(SWAY_TREE)],
                              ["get_config", json.dumps({"config": "bindsym Mod4+d exec wofi"})]])
    _tool(bindir, "hyprctl", [["monitors", json.dumps(HYPR_MONITORS)], ["clients", json.dumps(HYPR_CLIENTS)],
                              ["activeworkspace", json.dumps({"id": 4, "name": "4"})], ["binds", json.dumps(HYPR_BINDS)]])
    _tool(bindir, "wlrctl", [])
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("FAKE_LOG", str(log))

    def calls():
        return [json.loads(x) for x in log.read_text().splitlines()] if log.exists() else []
    return calls


def test_detect_picks_the_running_compositor(fake, monkeypatch):
    base = {"WAYLAND_DISPLAY": "wayland-1"}
    assert C.detect(base) is None
    assert C.detect({**base, "SWAYSOCK": "/run/sway.sock"}).name == "sway"
    assert C.detect({**base, "HYPRLAND_INSTANCE_SIGNATURE": "abc"}).name == "hyprland"
    both = {**base, "SWAYSOCK": "/s", "HYPRLAND_INSTANCE_SIGNATURE": "abc"}
    assert C.detect(both).name == "hyprland"
    assert C.detect({**both, "ORION_COMPOSITOR": "sway"}).name == "sway"
    # a socket without the tool is no session
    assert C.detect({**base, "SWAYSOCK": "/s"}, which=lambda _n: None) is None


def test_sway_backend_commands(fake):
    b = C.SwayBackend()
    outs = b.outputs()
    assert outs[0]["name"] == "eDP-1" and outs[0]["rect"]["width"] == 1280 and not outs[1]["active"]
    assert b.focused_window()["id"] == 7
    assert b.config_text() == "bindsym Mod4+d exec wofi"
    assert b.focus(7) and b.focus(9, minimised=True)
    for act in C.ACTIONS:
        assert b.window_action(act, 7)
    assert not b.window_action("explode", 7)
    assert b.set_keyboard("de", "nodeadkeys") and b.set_keyboard("gb")
    assert b.set_borders(True) and b.set_borders(False) and b.reload()
    assert b.cursor_move(10, 20) and b.click("button3")
    assert not b.set_screen_shader("/x.frag")       # Gravity mode needs Hyprland
    sent = [c[1:] for c in fake() if c[0] == "swaymsg"]
    assert ["[con_id=7] focus"] in sent
    assert ["[con_id=9] scratchpad show, floating disable, focus"] in sent
    assert ["[con_id=7] kill"] in sent and ["[con_id=7] move scratchpad"] in sent
    assert ["[con_id=7] fullscreen toggle"] in sent and ["[con_id=7] floating toggle"] in sent
    assert ["input type:keyboard xkb_layout de"] in sent and ["input type:keyboard xkb_variant nodeadkeys"] in sent
    assert ['input type:keyboard xkb_variant ""'] in sent
    assert ["[all] border normal 2"] in sent and ["[all] border pixel 2"] in sent and ["reload"] in sent
    assert ["seat", "-", "cursor", "set", "10", "20"] in sent
    i = sent.index(["seat", "-", "cursor", "press", "button3"])
    assert sent[i + 1] == ["seat", "-", "cursor", "release", "button3"]


def test_hyprland_backend_commands(fake):
    b = C.HyprlandBackend()
    outs = b.outputs()
    assert outs == [{"name": "eDP-1", "active": True, "focused": True,
                     "rect": {"x": 0, "y": 0, "width": 1280, "height": 800}, "scale": 2.0}]   # logical pixels
    ff = 0x55d0c1a2b3c0
    assert b.focused_window()["id"] == ff
    assert b.focus(ff)
    assert b.focus(0x55d0c1a2b400, minimised=True)
    for act in C.ACTIONS:
        assert b.window_action(act, ff)
    assert b.set_keyboard("fr", "") and b.reload() and b.set_borders(True)
    assert b.cursor_move(5, 6) and b.click("button1") and b.click("button5")
    assert b.button("button1", True) is False          # no IPC to hold a button: drags are refused, not faked
    assert b.set_screen_shader("/opt/orion/desktop/hyprland/gravity.frag") and b.set_screen_shader(None)
    assert b.config_text() is None
    binds = b.bindings()
    assert binds == [{"keys": "Mod4+d", "command": "exec wofi --show drun", "flags": []},
                     {"keys": "Mod4+Shift+q", "command": "kill", "flags": []}]
    calls = fake()
    hy = [c[1:] for c in calls if c[0] == "hyprctl"]
    a = "address:0x55d0c1a2b3c0"
    assert ["dispatch", "focuswindow", a] in hy
    assert ["--batch", "dispatch movetoworkspacesilent 4,address:0x55d0c1a2b400 ; "
            "dispatch focuswindow address:0x55d0c1a2b400"] in hy
    assert ["dispatch", "closewindow", a] in hy
    assert ["--batch", f"dispatch focuswindow {a} ; dispatch fullscreen 1"] in hy
    assert ["dispatch", "movetoworkspacesilent", f"special:minimized,{a}"] in hy
    assert ["dispatch", "togglefloating", a] in hy and ["dispatch", "movetoworkspace", f"r+1,{a}"] in hy
    assert ["--batch", "keyword input:kb_layout fr ; keyword input:kb_variant "] in hy
    assert ["dispatch", "movecursor", "5", "6"] in hy and ["reload"] in hy
    assert ["keyword", "decoration:screen_shader", "/opt/orion/desktop/hyprland/gravity.frag"] in hy
    assert ["keyword", "decoration:screen_shader", "[[EMPTY]]"] in hy
    wl = [c[1:] for c in calls if c[0] == "wlrctl"]
    assert ["pointer", "click", "left"] in wl and ["pointer", "scroll", "10", "0"] in wl


def test_hyprland_clients_become_a_sway_shaped_tree():
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", "os", "desktop"))
    import orion_windows as W
    tree = C.HyprlandBackend.tree_from_clients(HYPR_CLIENTS)
    wins = W.windows(tree)                                   # the HUD itself is left out, as on Sway
    by = {w["app"]: w for w in wins}
    assert set(by) == {"Firefox", "Calculator", "Code"}
    assert by["Calculator"]["minimised"] and by["Calculator"]["workspace"] is None
    assert by["Firefox"]["focused"] and by["Firefox"]["workspace"] == "2"
    assert by["Code"]["xwayland"]
    assert ld.windows_from_tree(tree)[0]["app"] in ("Firefox", "Calculator", "Code")
    assert C.HyprlandBackend.tree_from_clients(None)["nodes"] == []


def test_hyprland_events(tmp_path):
    ev = C.HyprlandBackend.event_from_line
    assert ev("activewindowv2>>55d0c1a2b3c0\n") == {"change": "focus", "container": {"id": 0x55d0c1a2b3c0}}
    assert ev("closewindow>>55d0c1a2b3c0") == {"change": "close", "container": {"id": 0x55d0c1a2b3c0}}
    assert ev("openwindow>>55d0c1a2b3c0,2,foot,~")["container"]["app_id"] == "foot"
    assert ev("activewindowv2>>,") is None and ev("workspace>>2") is None
    # the real event socket ($XDG_RUNTIME_DIR/hypr/<sig>/.socket2.sock)
    sockdir = tmp_path / "hypr" / "sig1"
    sockdir.mkdir(parents=True)
    path = str(sockdir / ".socket2.sock")
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(path)
    srv.listen(1)

    def serve():
        conn, _ = srv.accept()
        conn.sendall(b"workspace>>2\nactivewindowv2>>abc\ncloseabc\nclosewindow>>abc\n")
        conn.close()
    threading.Thread(target=serve, daemon=True).start()
    b = C.HyprlandBackend(env={"HYPRLAND_INSTANCE_SIGNATURE": "sig1", "XDG_RUNTIME_DIR": str(tmp_path)})
    assert b.socket_path() == path
    got = list(b.subscribe(["window"]))
    srv.close()
    assert [e["change"] for e in got] == ["focus", "close"] and got[0]["container"]["id"] == 0xABC
    assert list(b.subscribe(["binding"])) == []


def test_linux_desktop_uses_the_adapter_on_hyprland(fake, monkeypatch):
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-1")
    monkeypatch.setenv("HYPRLAND_INSTANCE_SIGNATURE", "abc")
    monkeypatch.delenv("SWAYSOCK", raising=False)
    monkeypatch.setattr(ld, "is_linux", lambda: True)
    assert ld.has_compositor() and not ld.is_sway()
    assert ld.backend().name == "hyprland"
    assert ld.screen_size() == (1280, 800)
    assert ld.active_window_title() == "Docs"
    assert {w["app"] for w in ld.list_windows()} == {"Firefox", "Calculator", "Code"}
    assert ld.focus_window(0x55d0c1a2b400)
    g = ld.SwayAutoGUI()
    g.PAUSE = 0
    g.click(3, 4)
    with pytest.raises(RuntimeError):
        g.dragTo(10, 10)
    hy = [c[1:] for c in fake() if c[0] == "hyprctl"]
    assert ["dispatch", "movecursor", "3", "4"] in hy
    assert ld.status()["compositor"] == "hyprland"


def test_desktop_copy_is_identical():
    here = os.path.dirname(os.path.abspath(__file__))
    src = os.path.join(here, "..", "compositor.py")
    copy = os.path.join(here, "..", "..", "..", "os", "desktop", "orion_compositor.py")
    with open(src, "rb") as a, open(copy, "rb") as b:
        assert a.read() == b.read(), "os/desktop/orion_compositor.py must be a byte copy of apps/daemon/compositor.py"
