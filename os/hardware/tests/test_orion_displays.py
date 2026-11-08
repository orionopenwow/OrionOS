"""Tests for the lid and workspace parts of os/lab/bin/orion-displays, with a stand-in for Sway.
(Headless Sway can't pretend to be a laptop panel, so these run against the captured laptop sample.)
   python3 -m unittest discover -s os/hardware/tests
"""

import copy
import importlib.machinery
import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPT = HERE.parents[1] / "lab" / "bin" / "orion-displays"


def load():
    loader = importlib.machinery.SourceFileLoader("orion_displays", str(SCRIPT))
    spec = importlib.util.spec_from_loader("orion_displays", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


class FakeSway:
    """Just enough of Sway: outputs, workspaces, and the output/workspace commands orion-displays sends."""

    def __init__(self, outputs, workspaces):
        self.outputs = copy.deepcopy(outputs)
        self.workspaces = workspaces          # [{"num", "name", "output", "visible", "focused"}]
        self.log = []

    def __call__(self, *args, check=True):
        if args[:2] == ("-t", "get_outputs"):
            return json.dumps(self.outputs)
        if args[:2] == ("-t", "get_workspaces"):
            return json.dumps(self.workspaces)
        for cmd in args[0].split("; "):
            self.log.append(cmd)
            w = cmd.split()
            if w[0] == "output" and w[2] in ("enable", "disable"):
                for o in self.outputs:
                    if o["name"] == w[1]:
                        o["active"] = w[2] == "enable"
            elif w[:2] == ["workspace", "number"]:
                self.current = int(w[2])
            elif w[:4] == ["move", "workspace", "to", "output"]:
                for ws in self.workspaces:
                    if ws["num"] == self.current:
                        ws["output"] = w[4]
        return ""


class Displays(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["XDG_RUNTIME_DIR"] = self.tmp.name
        os.environ["ORION_DISPLAYS_STORE"] = str(Path(self.tmp.name) / "displays.json")
        os.environ["ORION_KANSHI_CONFIG"] = str(Path(self.tmp.name) / "kanshi")
        self.d = load()
        self.laptop = json.loads((HERE / "samples" / "laptop-external.json").read_text())

    def tearDown(self):
        self.tmp.cleanup()

    def use(self, outputs, workspaces=()):
        fake = FakeSway(outputs, list(workspaces))
        self.d.swaymsg = fake
        return fake

    def test_lid_close_with_monitor_turns_panel_off(self):
        fake = self.use(self.laptop)
        outs = self.d.lid(self.d.get_outputs(), {"profiles": []}, "close")
        self.assertEqual(fake.log, ["output eDP-1 disable"])
        self.assertFalse(next(o for o in outs if o["name"] == "eDP-1")["active"])

    def test_lid_close_alone_does_nothing(self):
        single = json.loads((HERE / "samples" / "single.json").read_text())
        fake = self.use(single)
        self.d.lid(self.d.get_outputs(), {"profiles": []}, "close")
        self.assertEqual(fake.log, [])            # the only screen stays on; the system sleeps as usual

    def test_lid_open_restores_panel_as_saved(self):
        self.laptop[0]["active"] = False
        fake = self.use(self.laptop)
        store = {"profiles": [self.d.oh.auto_profile(json.loads((HERE / "samples" / "laptop-external.json").read_text()),
                                                     {"eDP-1": (302, 189)})]}
        self.d.lid(self.d.get_outputs(), store, "open")
        self.assertEqual(fake.log, ["output eDP-1 enable mode 2880x1800@90.000Hz position 0 0 scale 2 transform normal"])

    def test_lid_open_keeps_panel_off_when_the_layout_says_so(self):
        self.laptop[0]["active"] = False
        fake = self.use(self.laptop)
        store = {"profiles": [self.d.oh.layout_from_outputs(self.laptop, "monitor-only")]}
        self.d.lid(self.d.get_outputs(), store, "open")
        self.assertEqual(fake.log, [])

    def test_pin_moves_hud_workspace_and_keeps_what_was_shown(self):
        # everything started on the laptop panel; the monitor shows workspace 3 (the user's choice)
        wss = [{"num": 1, "name": "1", "output": "DP-1", "visible": False, "focused": False},
               {"num": 2, "name": "2", "output": "eDP-1", "visible": True, "focused": True},
               {"num": 3, "name": "3", "output": "DP-1", "visible": True, "focused": False}]
        fake = self.use(self.laptop, wss)
        plan = self.d.pin(self.d.get_outputs(), {"profiles": []})
        self.assertEqual(plan[1], "eDP-1")
        self.assertIn("workspace 1 output eDP-1", fake.log)
        self.assertIn("workspace 2 output DP-1 eDP-1", fake.log)          # with the primary as fallback
        self.assertEqual({w["num"]: w["output"] for w in fake.workspaces}, {1: "eDP-1", 2: "DP-1", 3: "DP-1"})
        # then: the monitor's workspace 3 is shown again, the HUD's workspace, and finally the focused one
        self.assertEqual(fake.log[-3:], ["workspace 3", "workspace 1", "workspace 2"])

    def test_pin_with_nothing_to_move_changes_no_focus(self):
        wss = [{"num": 1, "name": "1", "output": "eDP-1", "visible": True, "focused": True}]
        fake = self.use(self.laptop, wss)
        self.d.pin(self.d.get_outputs(), {"profiles": []})
        self.assertTrue(all(c.startswith("workspace ") and " output " in c for c in fake.log))

    def test_save_with_lid_shut_remembers_panel_on(self):
        lid = Path(self.tmp.name) / "lid" / "LID0"
        lid.mkdir(parents=True)
        (lid / "state").write_text("state:      closed\n")
        self.d.LID = lid.parent
        self.laptop[0]["active"] = False
        self.use(self.laptop, [])
        self.d.notify = lambda *a: None
        self.d.save("docked")
        store = json.loads(Path(os.environ["ORION_DISPLAYS_STORE"]).read_text())
        panel = next(o for o in store["profiles"][0]["outputs"] if o["criteria"].startswith("Samsung"))
        self.assertTrue(panel["enable"])
        self.assertIn("profile docked {", Path(os.environ["ORION_KANSHI_CONFIG"]).read_text())


if __name__ == "__main__":
    unittest.main()
