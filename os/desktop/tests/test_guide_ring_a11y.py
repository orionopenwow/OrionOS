"""Guide Mode's ring on OrionOS (orion_guide_ring.py, orion-guide-ring) and the screen reader on Sway (orion_a11y.py,
orion-a11y-check). No Sway, GTK or Orca needed.   python3 -m pytest os/desktop/tests"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
OS = HERE.parents[1]
sys.path.insert(0, str(HERE.parent))
import orion_a11y as A  # noqa: E402
import orion_guide_ring as R  # noqa: E402


# ------------------------------------------------------------------ the ring
def test_ring_reads_the_daemon_file_and_scales(tmp_path):
    p = R.state_path({"XDG_RUNTIME_DIR": str(tmp_path)})
    assert p == tmp_path / "orion-guide.json" and R.read_state(p) is None
    p.write_text(json.dumps({"active": True, "index": 1, "total": 4, "text": "Click Install.",
                             "target": {"x": 200, "y": 100, "w": 80, "h": 20}}))
    st = R.read_state(p)
    cx, cy, r = R.ring(st, 2.0)
    assert (cx, cy) == (120, 55) and r == 30.0
    assert R.caption(st) == "Step 2 of 4: Click Install."
    p.write_text(json.dumps({"active": False}))
    assert R.read_state(p) is None
    p.write_text("not json")
    assert R.read_state(p) is None


def test_no_ring_on_private_screens_or_without_a_target():
    assert R.ring({"active": True, "target": None}) is None
    assert R.ring({"active": True, "withheld": "x", "target": {"x": 1, "y": 1, "w": 1, "h": 1}}) is None
    assert R.ring({"active": True, "target": {"x": "a"}}) is None
    assert R.ring({"active": True, "target": {"x": 0, "y": 0, "w": 2, "h": 2}})[2] == R.MIN_SIZE / 2


def test_focused_scale():
    assert R.focused_scale([{"focused": False, "scale": 2}, {"focused": True, "scale": 1.5}]) == 1.5
    assert R.focused_scale(None) == 1.0


def test_ring_command_parses_and_quits_without_gtk(tmp_path):
    exe = OS / "lab/bin/orion-guide-ring"
    assert os.access(exe, os.X_OK)
    src = exe.read_text()
    assert "input_shape_combine_region" in src and "KeyboardMode.NONE" in src and "Layer.OVERLAY" in src
    # no gtk-layer-shell here (or no guide running): exits without hanging
    r = subprocess.run([sys.executable, str(exe), "--check"], capture_output=True, timeout=60,
                       env={**os.environ, "XDG_RUNTIME_DIR": str(tmp_path), "WAYLAND_DISPLAY": ""})
    assert r.returncode in (0, 1)


def test_installed_with_the_desktop():
    sh = (OS / "image/os-desktop.sh").read_text()
    assert "orion_guide_ring.py" in sh and "orion-guide-ring" in sh and "orion-a11y-check" in sh
    assert "python3-pyatspi" in sh and "gir1.2-gtklayershell-0.1" in sh


# ------------------------------------------------------------------ screen reader on Sway
def fake_run(answers):
    calls = []

    def run(argv):
        calls.append(argv)
        return answers.get(argv[0], (127, ""))
    return run, calls


GOOD_ENV = {"WAYLAND_DISPLAY": "wayland-1", "DBUS_SESSION_BUS_ADDRESS": "unix:path=/run/user/1000/bus"}
BUS_OK = (0, 's "unix:path=/run/user/1000/at-spi/bus_0,guid=abc"\n')


def test_bus_up_asks_the_accessibility_bus_and_falls_back_to_dbus_send():
    run, calls = fake_run({"busctl": BUS_OK})
    ok, addr = A.bus_up(run)
    assert ok and addr.startswith("unix:path=") and calls[0][:3] == ["busctl", "--user", "call"]
    run, calls = fake_run({"dbus-send": (0, 'string "unix:path=/tmp/x"')})
    assert A.bus_up(run)[0] and [c[0] for c in calls] == ["busctl", "dbus-send"]
    assert A.bus_up(fake_run({})[0]) == (False, "the accessibility bus didn't answer")


def test_check_all_good():
    run, _ = fake_run({"busctl": BUS_OK})
    rows = A.check(GOOD_ENV, run, lambda n: True, "QT_ACCESSIBILITY=1\n", reader_on=True, orca_running=True,
                   hud=True)
    ok, words = A.verdict(rows)
    assert ok and words == "The screen reader can work here."
    assert {r["id"] for r in rows} >= {"orca", "atspi", "bus", "bridge", "orca_running", "hud"}


def test_check_finds_what_breaks_orca():
    run, _ = fake_run({})
    env = {**GOOD_ENV, "NO_AT_BRIDGE": "1"}
    rows = A.check(env, run, lambda n: n != "orca", "export GTK_A11Y=none\n", reader_on=True, orca_running=False)
    ok, words = A.verdict(rows)
    by = {r["id"]: r for r in rows}
    assert not ok and not by["orca"]["ok"] and not by["bus"]["ok"] and not by["orca_running"]["ok"]
    assert "NO_AT_BRIDGE=1" in by["bridge"]["text"] and "GTK_A11Y=none (in session.env)" in by["bridge"]["text"]
    assert "sudo apt install orca" in words


def test_strip_disablers_keeps_other_lines():
    text = "ORION_SWAYFX=0\nexport NO_AT_BRIDGE=1\nGTK_A11Y='none'\n# note\nQT_ACCESSIBILITY=1\n"
    assert A.strip_disablers(text) == "ORION_SWAYFX=0\n# note\nQT_ACCESSIBILITY=1\n"


def test_hud_names():
    assert A.parse_apps(["Firefox", "Orion"]) and not A.parse_apps(["foot", "Thunar"])


def test_check_command_runs_and_the_hud_never_turns_the_bridge_off(tmp_path):
    exe = OS / "lab/bin/orion-a11y-check"
    r = subprocess.run([sys.executable, str(exe), "--json"], capture_output=True, text=True, timeout=60,
                       env={"PATH": "/nonexistent", "ORION_CONF_DIR": str(tmp_path), "HOME": str(tmp_path)})
    d = json.loads(r.stdout.strip().splitlines()[-1])
    assert r.returncode == 1 and not d["ok"] and d["rows"]
    (tmp_path / "session.env").write_text("NO_AT_BRIDGE=1\nX=1\n")
    subprocess.run([sys.executable, str(exe), "fix"], capture_output=True, timeout=60,
                   env={"PATH": "/usr/bin:/bin", "ORION_CONF_DIR": str(tmp_path), "HOME": str(tmp_path)})
    assert (tmp_path / "session.env").read_text() == "X=1\n"
    hud = (OS / "lab/bin/orion-hud").read_text()
    assert "unset NO_AT_BRIDGE GTK_A11Y" in hud and "NO_AT_BRIDGE=1" not in hud
    everyone = (OS / "everyone/orion_everyone.py").read_text()
    assert everyone.count("orca_bus_up()") >= 3       # defined once, called before both Orca starts
