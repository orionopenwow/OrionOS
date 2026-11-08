"""Tests for os/hardware/orion_hardware.py.   python3 -m unittest discover -s os/hardware/tests

The screen samples are `swaymsg -t get_outputs -r` output (sway 1.9) for a laptop with a monitor plugged in, a
desktop with two identical monitors (the second still off), and a laptop on its own. The lpstat samples were
captured from CUPS 2.4.7 (Ubuntu 24.04) with two queues added; one network printer was added to lpstat-e.txt.
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import orion_hardware as oh  # noqa: E402

S = HERE / "samples"
HAVE_TZDATA = Path("/usr/share/zoneinfo/zone1970.tab").is_file()


def sample(name):
    return json.loads((S / f"{name}.json").read_text())


def edid(width_mm, height_mm, dtd=True):
    """A 128-byte EDID with just the parts edid_size_mm reads."""
    b = bytearray(128)
    b[0:8] = b"\x00\xff\xff\xff\xff\xff\xff\x00"
    b[21], b[22] = width_mm // 10, height_mm // 10
    if dtd:
        b[54], b[55] = 0x01, 0x1d                       # a pixel clock: this is a detailed timing
        b[66], b[67] = width_mm & 0xFF, height_mm & 0xFF
        b[68] = ((width_mm >> 8) << 4) | (height_mm >> 8)
    return bytes(b)


class Keys(unittest.TestCase):
    def test_make_model_serial(self):
        k = oh.output_keys(sample("laptop-external"))
        self.assertEqual(k["DP-1"], "Dell Inc. DELL S2721DGF 7CDLN13")
        self.assertEqual(k["eDP-1"], "Samsung Display Corp. ATNA40YK07-0 Unknown")

    def test_identical_monitors_without_serials_fall_back_to_names(self):
        outs = sample("two-externals")
        for o in outs:
            o["serial"] = "Unknown"
        self.assertEqual(oh.output_keys(outs), {"DP-1": "DP-1", "HDMI-A-1": "HDMI-A-1"})

    def test_identical_monitors_with_serials_are_told_apart(self):
        k = oh.output_keys(sample("two-externals"))
        self.assertEqual(len(set(k.values())), 2)
        self.assertTrue(k["DP-1"].endswith("0x0000B2F1"))

    def test_headless_outputs_use_names(self):
        outs = [{"name": "HEADLESS-1", "make": "Unknown", "model": "Unknown", "serial": "Unknown"}]
        self.assertEqual(oh.output_keys(outs), {"HEADLESS-1": "HEADLESS-1"})


class Edid(unittest.TestCase):
    def test_detailed_timing_mm(self):
        self.assertEqual(oh.edid_size_mm(edid(302, 189)), (302, 189))
        self.assertEqual(oh.edid_size_mm(edid(597, 336)), (597, 336))

    def test_falls_back_to_cm(self):
        self.assertEqual(oh.edid_size_mm(edid(527, 296, dtd=False)), (520, 290))

    def test_not_an_edid(self):
        self.assertIsNone(oh.edid_size_mm(b"\x00" * 128))
        self.assertIsNone(oh.edid_size_mm(b""))

    def test_sysfs_lookup(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "card1-eDP-1"
            p.mkdir()
            (p / "edid").write_bytes(edid(302, 189))
            self.assertEqual(oh.sysfs_size_mm("eDP-1", Path(d)), (302, 189))
            self.assertIsNone(oh.sysfs_size_mm("DP-1", Path(d)))


class Scale(unittest.TestCase):
    def test_dense_laptop_panel_gets_2(self):
        edp = sample("laptop-external")[0]
        self.assertEqual(oh.choose_scale(edp, (302, 189)), 2)          # 14", 242 ppi

    def test_ordinary_monitor_gets_1(self):
        dell = sample("laptop-external")[1]
        self.assertEqual(oh.choose_scale(dell, (597, 336)), 1)         # 27" 1440p, 109 ppi
        self.assertEqual(oh.choose_scale(sample("single")[0], (600, 340)), 1)   # 27" 1080p panel

    def test_24in_4k_gets_150(self):
        lg = sample("two-externals")[0]
        self.assertEqual(oh.choose_scale(lg, (527, 296)), 1.5)         # 185 ppi: 150%, like Windows

    def test_fractional_steps_like_windows(self):
        laptop = sample("single")[0]                                   # 1920x1080 panel
        self.assertEqual(oh.choose_scale(laptop, (344, 194)), 1.25)    # 15.6": 141 ppi -> 125%
        self.assertEqual(oh.choose_scale(laptop, (294, 165)), 1.5)     # 13.3": 166 ppi -> 150%, still 1280 wide
        lg = sample("two-externals")[0]                                # 3840x2160 monitor
        self.assertEqual(oh.choose_scale(lg, (708, 398)), 1.25)        # 32": 138 ppi
        self.assertEqual(oh.choose_scale(lg, (900, 506)), 1)           # 40": 108 ppi
        self.assertEqual(oh.choose_scale(lg, (597, 336)), 1.5)         # 27": 163 ppi
        self.assertEqual(oh.choose_scale(lg, (477, 268)), 1.75)        # 21.5": 204 ppi
        self.assertEqual(oh.choose_scale(lg, (340, 190)), 2)           # 15.6" 4K: 287 ppi
        tiny = {"name": "eDP-1", "active": True, "current_mode": {"width": 1366, "height": 768}}
        self.assertEqual(oh.choose_scale(tiny, (200, 113)), 1)         # dense but too small to make bigger

    def test_unknown_size(self):
        edp, dell = sample("laptop-external")
        self.assertEqual(oh.choose_scale(edp, None), 1.5)              # a 2880-wide panel, size unknown
        self.assertEqual(oh.choose_scale(dell, None), 1)


class AutoProfile(unittest.TestCase):
    def test_laptop_left_external_right(self):
        outs = sample("laptop-external")
        p = oh.auto_profile(outs, {"eDP-1": (302, 189), "DP-1": (597, 336)})
        self.assertTrue(p["auto"])
        self.assertEqual(p["name"], "laptop-dell-s2721dgf")
        edp, dell = p["outputs"]
        self.assertEqual(edp["criteria"], "Samsung Display Corp. ATNA40YK07-0 Unknown")
        self.assertEqual((edp["position"], edp["scale"], edp["mode"]), ([0, 0], 2, "2880x1800@90.000Hz"))
        # the monitor starts where the panel's logical width (2880 / 2) ends
        self.assertEqual((dell["position"], dell["scale"], dell["mode"]), ([1440, 0], 1, "2560x1440@59.951Hz"))

    def test_two_externals_in_connector_order_native_modes(self):
        outs = list(reversed(sample("two-externals")))                 # order from sway doesn't matter
        p = oh.auto_profile(outs, {"DP-1": (527, 296), "HDMI-A-1": (527, 296)})
        a, b = p["outputs"]
        self.assertTrue(a["criteria"].endswith("B2F1") and b["criteria"].endswith("B3A7"))   # DP-1 then HDMI-A-1
        self.assertEqual(a["mode"], "3840x2160@60.000Hz")
        self.assertEqual(b["mode"], "3840x2160@60.000Hz")             # the off one: its biggest, fastest mode
        self.assertEqual((a["position"], b["position"]), ([0, 0], [2560, 0]))   # 3840 / 1.5
        self.assertEqual(p["name"], "lg-hdr-4k-lg-hdr-4k")

    def test_single(self):
        p = oh.auto_profile(sample("single"))
        self.assertEqual(p["name"], "laptop")
        self.assertEqual(p["outputs"], [{"criteria": "BOE 0x095F Unknown", "enable": True, "mode": "1920x1080@60.052Hz",
                                         "position": [0, 0], "scale": 1, "transform": "normal"}])

    def test_kanshi_text(self):
        store = oh.add_profile({"profiles": []}, oh.auto_profile(sample("laptop-external"), {"eDP-1": (302, 189)}))
        text = oh.render_kanshi(store)
        self.assertIn("profile laptop-dell-s2721dgf {", text)
        self.assertIn('\toutput "Samsung Display Corp. ATNA40YK07-0 Unknown" enable mode 2880x1800@90.000Hz '
                      'position 0,0 scale 2', text)
        self.assertIn('\toutput "Dell Inc. DELL S2721DGF 7CDLN13" enable mode 2560x1440@59.951Hz position 1440,0 scale 1',
                      text)
        self.assertIn("\texec orion-displays changed", text)
        self.assertEqual(text.count("{"), text.count("}"))

    def test_headless_names_unquoted(self):
        outs = [{"name": "HEADLESS-1", "make": "Unknown", "model": "Unknown", "serial": "Unknown", "active": True,
                 "current_mode": {"width": 1280, "height": 720, "refresh": 0}}]
        text = oh.render_kanshi({"profiles": [oh.auto_profile(outs)]})
        self.assertIn("\toutput HEADLESS-1 enable mode 1280x720 position 0,0 scale 1", text)


class SaveAndMatch(unittest.TestCase):
    def test_layout_from_outputs(self):
        outs = sample("two-externals")
        p = oh.layout_from_outputs(outs, "Desk at home")
        self.assertEqual(p["name"], "desk-at-home")
        self.assertFalse(p["auto"])
        self.assertEqual(p["outputs"][0], {"criteria": "LG Electronics LG HDR 4K 0x0000B2F1", "enable": True,
                                           "mode": "3840x2160@60.000Hz", "position": [0, 0], "scale": 1,
                                           "transform": "normal"})
        self.assertEqual(p["outputs"][1], {"criteria": "LG Electronics LG HDR 4K 0x0000B3A7", "enable": False})

    def test_saved_replaces_auto_for_same_screens_and_comes_first(self):
        store = {"profiles": []}
        oh.add_profile(store, oh.auto_profile(sample("single")))
        oh.add_profile(store, oh.auto_profile(sample("laptop-external")))
        oh.add_profile(store, oh.layout_from_outputs(sample("laptop-external"), "office"))
        self.assertEqual([p["name"] for p in store["profiles"]], ["office", "laptop"])
        self.assertEqual(oh.find_profile(store, sample("laptop-external"))["name"], "office")
        self.assertEqual(oh.find_profile(store, sample("single"))["name"], "laptop")
        self.assertIsNone(oh.find_profile(store, sample("two-externals")))

    def test_fractional_scale_kept(self):
        outs = sample("single")
        outs[0]["scale"] = 1.25
        self.assertEqual(oh.layout_from_outputs(outs, "x")["outputs"][0]["scale"], 1.25)

    def test_unique_name(self):
        store = {"profiles": [{"name": "laptop", "outputs": []}, {"name": "laptop-2", "outputs": []}]}
        self.assertEqual(oh.unique_name(store, "laptop"), "laptop-3")
        self.assertEqual(oh.unique_name(store, "desk"), "desk")

    def test_sway_commands(self):
        outs = sample("two-externals")
        p = oh.layout_from_outputs(outs, "x")
        p["outputs"].reverse()                                         # a disable listed first still runs last
        cmds = oh.sway_commands(p, outs)
        self.assertEqual(cmds[0], "output DP-1 enable mode 3840x2160@60.000Hz position 0 0 scale 1 transform normal")
        self.assertEqual(cmds[-1], "output HDMI-A-1 disable")
        with self.assertRaises(KeyError):
            oh.sway_commands(p, sample("single"))

    def test_store_roundtrip(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "orion" / "displays.json"
            self.assertEqual(oh.load_store(path)["profiles"], [])
            store = oh.add_profile(oh.load_store(path), oh.auto_profile(sample("single")))
            oh.save_store(path, store)
            self.assertEqual(oh.load_store(path), store)
            path.write_text("{broken")
            self.assertEqual(oh.load_store(path)["profiles"], [])
            k = Path(d) / "kanshi" / "config"
            self.assertTrue(oh.write_if_changed(k, "a"))
            self.assertFalse(oh.write_if_changed(k, "a"))


class PrimaryAndWorkspaces(unittest.TestCase):
    def test_laptop_panel_is_primary_by_default(self):
        outs = sample("laptop-external")
        self.assertEqual(oh.primary_output(outs), "eDP-1")
        self.assertEqual(oh.primary_output(outs, "Dell Inc. DELL S2721DGF 7CDLN13"), "DP-1")
        self.assertEqual(oh.primary_output(outs, "DP-1"), "DP-1")
        self.assertEqual(oh.primary_output(outs, "Some Gone Monitor 1"), "eDP-1")

    def test_lid_closed_primary_moves_to_external(self):
        outs = sample("laptop-external")
        outs[0]["active"] = False
        self.assertEqual(oh.primary_output(outs), "DP-1")

    def test_leftmost_without_panel(self):
        outs = sample("two-externals")
        outs[1].update({"active": True, "rect": {"x": -1920, "y": 0, "width": 1920, "height": 1080}})
        self.assertEqual(oh.primary_output(outs), "HDMI-A-1")
        self.assertIsNone(oh.primary_output([]))

    def test_plan_two_screens(self):
        outs = sample("laptop-external")
        plan = oh.workspace_plan(outs, "eDP-1")
        self.assertEqual(plan[1], "eDP-1")
        self.assertEqual([plan[n] for n in range(2, 6)], ["DP-1"] * 4)
        self.assertEqual([plan[n] for n in range(6, 10)], ["eDP-1"] * 4)

    def test_plan_one_screen(self):
        self.assertEqual(set(oh.workspace_plan(sample("single"), "eDP-1").values()), {"eDP-1"})
        self.assertEqual(len(oh.workspace_plan(sample("single"), "eDP-1")), 9)

    def test_plan_three_screens(self):
        outs = sample("laptop-external") + [
            {"name": "DP-2", "active": True, "rect": {"x": 4000, "y": 0}},
        ]
        plan = oh.workspace_plan(outs, "eDP-1")
        self.assertEqual(plan[1], "eDP-1")
        self.assertEqual([plan[n] for n in range(2, 10)], ["DP-1"] * 4 + ["DP-2"] * 4)

    def test_lid_state(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "LID0").mkdir()
            (Path(d) / "LID0" / "state").write_text("state:      closed\n")
            self.assertTrue(oh.lid_closed(Path(d)))
            (Path(d) / "LID0" / "state").write_text("state:      open\n")
            self.assertFalse(oh.lid_closed(Path(d)))
            self.assertFalse(oh.lid_closed(Path(d) / "missing"))

    def test_summary_json(self):
        outs = sample("laptop-external")
        store = oh.add_profile({"profiles": []}, oh.auto_profile(outs))
        info = oh.outputs_summary(outs, store)
        json.dumps(info)
        self.assertEqual(info["primary"], "eDP-1")
        self.assertEqual(info["current_profile"], "laptop-dell-s2721dgf")
        dp = next(o for o in info["outputs"] if o["name"] == "DP-1")
        self.assertEqual(dp["workspaces"], [2, 3, 4, 5])
        self.assertEqual(dp["modes"][0], "2560x1440@59.951Hz")
        self.assertFalse(dp["internal"])
        self.assertTrue(info["profiles"][0]["current"])


class Lpstat(unittest.TestCase):
    def parse(self):
        return oh.parse_lpstat(*((S / f).read_text() for f in ("lpstat-l-p.txt", "lpstat-v.txt", "lpstat-d.txt",
                                                                 "lpstat-e.txt")))

    def test_captured(self):
        info = self.parse()
        self.assertEqual(info["default"], "Office_Laser")
        names = [p["name"] for p in info["printers"]]
        self.assertEqual(names, ["Office_Laser", "Label", "HP_LaserJet_MFP_M140w_5F1A2B"])   # default, added, found
        office, label, hp = info["printers"]
        self.assertEqual((office["state"], office["enabled"], office["default"]), ("idle", True, True))
        self.assertEqual((office["description"], office["location"]), ("Office laser", "Upstairs"))
        self.assertEqual(office["uri"], "ipp://192.0.2.10/ipp/print")
        self.assertNotIn("reason", office)                          # "Form mounted:" etc. aren't reasons
        self.assertEqual((label["state"], label["enabled"], label["reason"]), ("disabled", False, "Out of labels"))
        self.assertEqual(label["uri"], "socket://192.0.2.11")
        self.assertEqual((hp["added"], hp["state"], hp["uri"]), (False, "idle", None))
        self.assertEqual(hp["description"], "HP LaserJet MFP M140w 5F1A2B")

    def test_printing_and_no_default(self):
        info = oh.parse_lpstat("printer Office is now printing Office-12.  enabled since Fri Oct 16 10:54:33 2026\n"
                               "\tDescription: Office\n",
                               "device for Office: ipp://office.local/ipp/print\n",
                               "no system default destination\n", "")
        self.assertIsNone(info["default"])
        p = info["printers"][0]
        self.assertEqual((p["state"], p["job"], p["uri"]), ("printing", "Office-12", "ipp://office.local/ipp/print"))

    def test_nothing(self):
        self.assertEqual(oh.parse_lpstat("", "", "lpstat: Scheduler is not running.\n", ""),
                         {"default": None, "printers": []})


class Paper(unittest.TestCase):
    def test_country(self):
        for cc in ("US", "CA", "MX", "PH", "CL", "us"):
            self.assertEqual(oh.paper_for_country(cc), "letter", cc)
        for cc in ("GB", "DE", "AU", "JP", "BR", "IN", None, ""):
            self.assertEqual(oh.paper_for_country(cc), "a4", cc)

    @unittest.skipUnless(HAVE_TZDATA, "tzdata not installed")
    def test_zone(self):
        self.assertEqual(oh.paper_for_zone("America/New_York"), "letter")
        self.assertEqual(oh.paper_for_zone("America/Toronto"), "letter")
        self.assertEqual(oh.paper_for_zone("America/Mexico_City"), "letter")
        self.assertEqual(oh.paper_for_zone("US/Pacific"), "letter")          # an old alias name
        self.assertEqual(oh.paper_for_zone("Europe/London"), "a4")
        self.assertEqual(oh.paper_for_zone("America/Sao_Paulo"), "a4")
        self.assertEqual(oh.paper_for_zone("Australia/Sydney"), "a4")
        self.assertEqual(oh.paper_for_zone("UTC"), "a4")
        self.assertEqual(oh.paper_for_zone("Etc/GMT+5"), "a4")

    @unittest.skipUnless(HAVE_TZDATA, "tzdata not installed")
    def test_zone_without_os_basics(self):
        real = oh._basics
        oh._basics = lambda: None
        try:
            self.assertEqual(oh.paper_for_zone("America/Toronto"), "letter")
            self.assertEqual(oh.paper_for_zone("Europe/Paris"), "a4")
            self.assertEqual(oh.paper_for_zone("UTC"), "a4")
        finally:
            oh._basics = real


if __name__ == "__main__":
    unittest.main()
