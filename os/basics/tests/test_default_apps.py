"""Tests for os/basics/orion_default_apps.py ("Try with Craft").   python3 -m unittest discover -s os/basics/tests"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import orion_default_apps as da  # noqa: E402

SYSTEM_LIST = """# OrionOS default apps
[Default Applications]
application/pdf=org.gnome.Evince.desktop;
image/png=org.gnome.eog.desktop;
text/plain=org.xfce.mousepad.desktop;

[Added Associations]
application/pdf=org.gnome.Evince.desktop;firefox.desktop;
"""


class TryWithCraft(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.env = {"HOME": str(self.tmp), "XDG_CONFIG_HOME": str(self.tmp / "config"),
                    "XDG_DATA_HOME": str(self.tmp / "data"), "XDG_DATA_DIRS": str(self.tmp / "sys")}
        self.old = {k: os.environ.get(k) for k in self.env}
        os.environ.update(self.env)
        (self.tmp / "config").mkdir()
        da.mimeapps_path().write_text(SYSTEM_LIST)

    def tearDown(self):
        for k, v in self.old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def install(self, kind):
        d = self.tmp / "data" / "flatpak/exports/share/applications"
        d.mkdir(parents=True, exist_ok=True)
        (d / da.desktop_file(kind)).write_text("[Desktop Entry]\n")

    def test_off_by_default_and_needs_the_app(self):
        st = da.status()
        self.assertFalse(st["pdf"]["on"] or st["images"]["on"])
        self.assertFalse(st["pdf"]["installed"])
        ok, msg = da.set_try_craft("pdf", True)
        self.assertFalse(ok)
        self.assertIn("Get apps", msg)
        self.assertEqual(da.mimeapps_path().read_text(), SYSTEM_LIST)        # untouched
        self.assertFalse(da.set_try_craft("video", True)[0])

    def test_on_then_off_restores_exactly(self):
        self.install("pdf")
        ok, msg = da.set_try_craft("pdf", True)
        self.assertTrue(ok, msg)
        lines = da.parse(da.mimeapps_path().read_text())
        self.assertEqual(da.get(lines, "Default Applications", "application/pdf"),
                         "ai.storyteller.pdfcraft.desktop;org.gnome.Evince.desktop;")
        self.assertEqual(da.get(lines, "Default Applications", "application/x-pdf"),
                         "ai.storyteller.pdfcraft.desktop;org.gnome.Evince.desktop;")   # fallback as 2nd choice
        self.assertEqual(da.get(lines, "Added Associations", "application/pdf"),
                         "ai.storyteller.pdfcraft.desktop;org.gnome.Evince.desktop;firefox.desktop;")
        self.assertEqual(da.get(lines, "Default Applications", "image/png"), "org.gnome.eog.desktop;")  # other type
        self.assertTrue(da.status()["pdf"]["on"])
        self.assertTrue(da.set_try_craft("pdf", True)[0])                    # twice is fine
        ok, _ = da.set_try_craft("pdf", False)
        self.assertTrue(ok)
        self.assertEqual(da.mimeapps_path().read_text(), SYSTEM_LIST)
        self.assertFalse(da.status()["pdf"]["on"])

    def test_off_keeps_a_later_choice_of_yours(self):
        self.install("images")
        da.set_try_craft("images", True)
        lines = da.put(da.parse(da.mimeapps_path().read_text()), "Default Applications", "image/png", "gimp.desktop;")
        da.mimeapps_path().write_text(da.render(lines))
        da.set_try_craft("images", False)
        lines = da.parse(da.mimeapps_path().read_text())
        self.assertEqual(da.get(lines, "Default Applications", "image/png"), "gimp.desktop;")
        self.assertIsNone(da.get(lines, "Default Applications", "image/webp"))   # we added it, we removed it

    def test_no_file_yet(self):
        da.mimeapps_path().unlink()
        self.install("pdf")
        self.assertTrue(da.set_try_craft("pdf", True)[0])
        text = da.mimeapps_path().read_text()
        self.assertIn("[Default Applications]\napplication/pdf=ai.storyteller.pdfcraft.desktop;", text)
        da.set_try_craft("pdf", False)
        lines = da.parse(da.mimeapps_path().read_text())
        self.assertIsNone(da.get(lines, "Default Applications", "application/pdf"))

    def test_command_line(self):
        env = {**os.environ, **self.env}
        out = subprocess.run([sys.executable, str(HERE.parent / "orion_default_apps.py"), "status", "--json"],
                             capture_output=True, text=True, env=env)
        self.assertEqual(out.returncode, 0)
        self.assertEqual(set(json.loads(out.stdout)), {"pdf", "images"})
        out = subprocess.run([sys.executable, str(HERE.parent / "orion_default_apps.py"), "on", "pdf"],
                             capture_output=True, text=True, env=env)
        self.assertEqual(out.returncode, 1)
        self.assertIn("isn't installed", out.stdout)


if __name__ == "__main__":
    unittest.main()
