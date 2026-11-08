"""Tests for os/basics/orion_basics.py.   python3 -m unittest discover -s os/basics/tests

Test secrets are joined from pieces at runtime, so no realistic-looking key is written in this file ([fake-secrets]).
"""

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import orion_basics as ob  # noqa: E402

REAL_ZONEINFO = Path("/usr/share/zoneinfo")
HAVE_TZDATA = (REAL_ZONEINFO / "zone1970.tab").is_file() and (REAL_ZONEINFO / "tzdata.zi").is_file()
SCRIPT = HERE.parent / "orion_basics.py"


def fake_zoneinfo(root: Path) -> Path:
    """A tiny tz database with the same file formats as tzdata's."""
    (root / "Europe").mkdir(parents=True)
    (root / "Europe" / "London").write_bytes(b"TZif")
    (root / "zone1970.tab").write_text(
        "# comment\n"
        "GB,GG,IM,JE\t+513030-0000731\tEurope/London\n"
        "US\t+404251-0740023\tAmerica/New_York\tEastern (most areas)\n"
        "US\t+415100-0873900\tAmerica/Chicago\tCentral (most areas)\n"
        "AU\t-3352+15113\tAustralia/Sydney\tNew South Wales (most areas)\n"
        "IN\t+2232+08822\tAsia/Kolkata\n", encoding="utf-8")
    (root / "zone.tab").write_text(
        "NO\t+5955+01045\tEurope/Oslo\n"
        "JE\t+491101-0020624\tEurope/Jersey\n", encoding="utf-8")
    (root / "tzdata.zi").write_text(
        "# version x\nZ Europe/London -0:1:15 - LMT 1847 D\n"
        "L Asia/Kolkata Asia/Calcutta\nL America/New_York US/Eastern\nL Europe/London Europe/Belfast\n"
        "L Europe/Belfast Europe/Chained\n", encoding="utf-8")
    (root / "iso3166.tab").write_text("GB\tBritain (UK)\nUS\tUnited States\nNO\tNorway\nIN\tIndia\n"
                                      "AU\tAustralia\nJE\tJersey\n", encoding="utf-8")
    return root


class ZoneTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.zi = fake_zoneinfo(Path(self.tmp.name) / "zoneinfo")

    def tearDown(self):
        self.tmp.cleanup()

    def test_iso6709_minutes_and_seconds(self):
        self.assertEqual(ob.parse_iso6709("+5130-00007"), (51.5, -7 / 60))
        lat, lon = ob.parse_iso6709("+404251-0740023")
        self.assertAlmostEqual(lat, 40.7142, places=3)
        self.assertAlmostEqual(lon, -74.0064, places=3)
        lat, lon = ob.parse_iso6709("-3352+15113")
        self.assertAlmostEqual(lat, -33.8667, places=3)
        self.assertAlmostEqual(lon, 151.2167, places=3)
        with self.assertRaises(ValueError):
            ob.parse_iso6709("51N 0W")

    def test_coords_direct_zone_tab_and_links(self):
        lat, lon = ob.zone_coords("Europe/London", self.zi)
        self.assertAlmostEqual(lat, 51.508, places=2)
        self.assertAlmostEqual(lon, -0.125, places=2)
        self.assertAlmostEqual(ob.zone_coords("Europe/Oslo", self.zi)[0], 59.9167, places=3)   # only in zone.tab
        self.assertEqual(ob.zone_coords("Asia/Calcutta", self.zi), ob.zone_coords("Asia/Kolkata", self.zi))
        self.assertEqual(ob.zone_coords("US/Eastern", self.zi), ob.zone_coords("America/New_York", self.zi))
        self.assertEqual(ob.zone_coords("Europe/Chained", self.zi), ob.zone_coords("Europe/London", self.zi))
        self.assertEqual(ob.zone_coords("posix/Europe/London", self.zi), ob.zone_coords("Europe/London", self.zi))

    def test_coords_none_for_placeless_zones(self):
        for z in ("UTC", "Etc/UTC", "Etc/GMT+3", "Nowhere/Atlantis", ""):
            self.assertIsNone(ob.zone_coords(z, self.zi), z)

    def test_zone_exists_rejects_paths_and_junk(self):
        self.assertTrue(ob.zone_exists("Europe/London", self.zi))
        self.assertTrue(ob.zone_exists("Asia/Calcutta", self.zi))    # a link
        self.assertTrue(ob.zone_exists("UTC", self.zi))
        for bad in ("../etc/passwd", "Europe/../../x", "zone1970.tab", "Europe/London; rm -rf /", "", "Mars/Base"):
            self.assertFalse(ob.zone_exists(bad, self.zi), bad)

    def test_locale_country(self):
        self.assertEqual(ob.locale_country("en_GB.UTF-8"), "GB")
        self.assertEqual(ob.locale_country("de_DE@euro"), "DE")
        self.assertEqual(ob.locale_country("ast_ES.UTF-8"), "ES")
        for none in ("C.UTF-8", "POSIX", "C", "", "en"):
            self.assertIsNone(ob.locale_country(none), none)

    def test_zones_for_country_primary_first(self):
        self.assertEqual(ob.zones_for_country("US", self.zi), ["America/New_York", "America/Chicago"])
        # Jersey's own row (zone.tab) comes before the shared London row where JE is only a secondary country
        self.assertEqual(ob.zones_for_country("JE", self.zi), ["Europe/Jersey", "Europe/London"])
        self.assertEqual(ob.zones_for_country("ZZ", self.zi), [])

    def test_chooser_lines(self):
        lines = ob.chooser_lines("GB", self.zi)
        self.assertEqual(lines[0], "Europe/London  ·  Britain (UK)")
        self.assertIn("UTC  ·  Coordinated Universal Time", lines)
        self.assertEqual(len(lines), len(set(lines)))
        self.assertTrue(all(l.split("  ·  ")[0] for l in lines))
        rest = [l for l in ob.chooser_lines(None, self.zi) if not l.startswith("UTC")]
        self.assertEqual(rest, sorted(rest))

    def test_parse_geoip(self):
        xml = ("<Response><Ip>192.0.2.1</Ip><Status>OK</Status><CountryCode>GB</CountryCode>"
               "<TimeZone>Europe/London</TimeZone></Response>")
        self.assertEqual(ob.parse_geoip(xml, self.zi), "Europe/London")
        self.assertIsNone(ob.parse_geoip(xml.replace("Europe/London", "../../etc/shadow"), self.zi))
        self.assertIsNone(ob.parse_geoip("<Response><Status>ERROR</Status></Response>", self.zi))
        self.assertIsNone(ob.parse_geoip("", self.zi))

    def test_current_zone_from_symlink(self):
        lt = Path(self.tmp.name) / "localtime"
        os.symlink("/usr/share/zoneinfo/Europe/London", lt)
        self.assertEqual(ob.current_zone(str(lt), "/nonexistent"), "Europe/London")
        tzf = Path(self.tmp.name) / "timezone"
        tzf.write_text("America/Chicago\n")
        self.assertEqual(ob.current_zone("/nonexistent", str(tzf)), "America/Chicago")
        self.assertEqual(ob.current_zone("/nonexistent", "/nonexistent"), "UTC")


@unittest.skipUnless(HAVE_TZDATA, "tzdata not installed")
class RealTzdataTests(unittest.TestCase):
    """Against this machine's tz database: every zone in the tables has a sane position."""

    def test_every_table_zone_has_coords(self):
        table = ob.zone_table(REAL_ZONEINFO)
        self.assertGreater(len(table), 300)
        for z in table:
            lat, lon = ob.zone_coords(z, REAL_ZONEINFO)
            self.assertTrue(-90 <= lat <= 90 and -180 <= lon <= 180, z)

    def test_known_cities(self):
        for z, (lat, lon) in {"Europe/London": (51.5, -0.1), "America/New_York": (40.7, -74.0),
                              "Australia/Sydney": (-33.9, 151.2), "Asia/Calcutta": (22.5, 88.4),
                              "Asia/Tokyo": (35.7, 139.7), "America/Sao_Paulo": (-23.5, -46.6)}.items():
            got = ob.zone_coords(z, REAL_ZONEINFO)
            self.assertIsNotNone(got, z)
            self.assertAlmostEqual(got[0], lat, delta=0.2, msg=z)
            self.assertAlmostEqual(got[1], lon, delta=0.2, msg=z)

    def test_every_linked_name_resolves_or_is_placeless(self):
        for link in ob.zone_links(REAL_ZONEINFO):
            c = ob.zone_coords(link, REAL_ZONEINFO)
            if c is None:   # only placeless aliases (UTC, GMT, Etc/...) may lack a position
                self.assertRegex(ob.canonical_zone(link, REAL_ZONEINFO), r"^(Etc/|UTC|GMT|Factory|[A-Z]{3,4}\d?[A-Z]*$)", link)

    def test_suggest_for_locale(self):
        self.assertEqual(ob.zones_for_country("GB", REAL_ZONEINFO)[0], "Europe/London")
        self.assertIn("Europe/Berlin", ob.zones_for_country("DE", REAL_ZONEINFO))


class SecretFilterTests(unittest.TestCase):
    def setUp(self):
        self.rules = ob.SecretRules.load()   # the daemon's secret_shield_rules.json from this repo

    def test_rules_file_is_the_daemons(self):
        self.assertGreater(len(self.rules.rules), 10)

    def test_keys_are_caught(self):
        samples = {
            "openai-api-key": "sk-" + "proj-" + "Q7rT2mX9vL4kP8wZ3nB6yH1cF5dJ0sGa",
            "anthropic-api-key": "sk-" + "ant-" + "api03-" + "Zq8Lr2Wm5Xp9Tn4Kv7Hs1Jd6",
            "github-token": "gh" + "p_" + "aB3dE6gH9jK2mN5pQ8sT1vW4yZ7bC0eF3hJ6",
            "private-key": "-----BEGIN " + "OPENSSH PRIVATE KEY" + "-----",
            "aws-access-key": "AK" + "IA" + "QX7RT2MZ9VL4KP8W",
        }
        for rid, text in samples.items():
            self.assertIsNotNone(self.rules.match("my key: " + text), rid)

    def test_password_assignment_caught_placeholder_not(self):
        self.assertIsNotNone(self.rules.match("DB_PASSWORD=" + "Tq7vR2mLx9" + "pW4zKs3n"))
        self.assertIsNone(self.rules.match("password=changeme"))
        self.assertIsNone(self.rules.match("password=${DB_PASSWORD}"))

    def test_ordinary_text_kept(self):
        for text in ("Meeting moved to 3pm, see you there", "https://example.com/docs?page=2",
                     "def main():\n    return 0\n", "sk-learn is a Python library", ""):
            self.assertIsNone(self.rules.match(text), text)

    def test_fallback_rules_when_file_missing(self):
        r = ob.SecretRules({"rules": ob.FALLBACK_RULES})
        self.assertIsNotNone(r.match("-----BEGIN " + "RSA PRIVATE KEY" + "-----"))
        self.assertIsNone(r.match("hello"))


class CommandLineTests(unittest.TestCase):
    def run_cli(self, *args, stdin=b"", env=None):
        e = dict(os.environ, **(env or {}))
        return subprocess.run([sys.executable, "-I", str(SCRIPT), *args], input=stdin, capture_output=True, env=e)

    def test_secret_exit_codes(self):
        key = ("sk-" + "proj-" + "Q7rT2mX9vL4kP8wZ3nB6yH1cF5dJ0sGa").encode()
        hit = self.run_cli("secret", stdin=b"token " + key)
        self.assertEqual(hit.returncode, 0)
        self.assertEqual(hit.stdout.strip(), b"openai-api-key")
        self.assertNotIn(key, hit.stdout + hit.stderr)          # the secret itself is never echoed
        self.assertEqual(self.run_cli("secret", stdin=b"lunch at noon").returncode, 1)
        self.assertEqual(self.run_cli("secret", stdin=b"\x89PNG\r\n\x1a\n\xff\xfe").returncode, 1)   # an image

    def test_coords_cli(self):
        with tempfile.TemporaryDirectory() as t:
            zi = fake_zoneinfo(Path(t) / "zi")
            r = self.run_cli("coords", "Europe/London", env={"ORION_ZONEINFO": str(zi)})
            self.assertEqual(r.stdout.decode().strip(), "51.51 -0.13")
            self.assertEqual(self.run_cli("coords", "UTC", env={"ORION_ZONEINFO": str(zi)}).returncode, 1)
            r = self.run_cli("geoip", stdin=b"<TimeZone>Asia/Calcutta</TimeZone>", env={"ORION_ZONEINFO": str(zi)})
            self.assertEqual(r.stdout.decode().strip(), "Asia/Calcutta")
            r = self.run_cli("suggest", "en_US.UTF-8", env={"ORION_ZONEINFO": str(zi)})
            self.assertEqual(r.stdout.decode().split(), ["America/New_York", "America/Chicago"])


if __name__ == "__main__":
    unittest.main()
