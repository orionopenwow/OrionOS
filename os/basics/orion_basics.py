#!/usr/bin/env python3
"""OrionOS everyday basics: the small offline helpers behind the clock, night light and clipboard scripts.

Everything here works without the network:
  * time zone -> approximate latitude/longitude, read from the tz database's own tables
    (/usr/share/zoneinfo/zone1970.tab and zone.tab give each zone's main city in ISO 6709 form),
    so the night light knows when the sun sets without asking any web service where you are;
  * the list of time zones for the chooser, with the country names, and the zones that match your
    language setting's country (en_GB -> Europe/London) shown first;
  * reading the answer of the opt-in internet lookup (Ubuntu's geoip service), checked against the zones
    that really exist here;
  * the clipboard's secret filter: Secret Shield's patterns (apps/daemon/secret_shield_rules.json, the same
    file the daemon and the git hook use) decide whether a copied text looks like a key or password, so it is
    never written to the clipboard history.

Used by os/lab/bin/orion-timezone, orion-nightlight and orion-clipboard. Command line:
  orion_basics.py coords [ZONE]        "LAT LON" (decimal degrees), exit 1 when the zone has no place (UTC)
  orion_basics.py current              the system's time zone (from /etc/localtime)
  orion_basics.py zones [CC]           chooser lines "Zone  ·  Country", the country CC's zones first
  orion_basics.py suggest [LANG]       zones for the country in a locale name such as en_GB.UTF-8
  orion_basics.py valid ZONE           exit 0 when ZONE exists in the tz database
  orion_basics.py geoip                read Ubuntu geoip XML on stdin, print the time zone (exit 1 if none)
  orion_basics.py secret               read text on stdin; exit 0 and print the rule id if it looks secret,
                                       exit 1 if it looks safe to keep
"""

from __future__ import annotations

import json
import math
import os
import re
import sys
from pathlib import Path
from typing import Iterable, Optional

ZONEINFO = Path(os.environ.get("ORION_ZONEINFO", "/usr/share/zoneinfo"))
RULES_CANDIDATES = (
    os.environ.get("ORION_SECRET_RULES", ""),
    "/opt/orion/daemon/secret_shield_rules.json",
    str(Path(__file__).resolve().parents[2] / "apps" / "daemon" / "secret_shield_rules.json"),
)
ZONE_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_+-]*(/[A-Za-z0-9_+-]+)*$")


# =====================================================================  time zones
def parse_iso6709(s: str) -> tuple[float, float]:
    """'+5130-00007' or '+404251-0740023' -> (51.5, -0.1167). Degrees, minutes and optional seconds."""
    m = re.fullmatch(r"([+-])(\d{2})(\d{2})(\d{2})?([+-])(\d{3})(\d{2})(\d{2})?", s.strip())
    if not m:
        raise ValueError(f"not an ISO 6709 position: {s!r}")
    lat = int(m[2]) + int(m[3]) / 60 + int(m[4] or 0) / 3600
    lon = int(m[6]) + int(m[7]) / 60 + int(m[8] or 0) / 3600
    return (-lat if m[1] == "-" else lat, -lon if m[5] == "-" else lon)


def _tab_rows(name: str, zoneinfo: Path) -> Iterable[list[str]]:
    try:
        text = (zoneinfo / name).read_text(encoding="utf-8")
    except OSError:
        return
    for line in text.splitlines():
        if line and not line.startswith("#"):
            cols = line.split("\t")
            if len(cols) >= 3:
                yield cols


def zone_table(zoneinfo: Path = ZONEINFO) -> dict[str, tuple[list[str], str]]:
    """zone -> ([country codes], ISO 6709 position). zone1970.tab first, zone.tab fills the gaps."""
    out: dict[str, tuple[list[str], str]] = {}
    for name in ("zone1970.tab", "zone.tab"):
        for cols in _tab_rows(name, zoneinfo):
            out.setdefault(cols[2], (cols[0].split(","), cols[1]))
    return out


def zone_links(zoneinfo: Path = ZONEINFO) -> dict[str, str]:
    """Old or alias names -> the zone they point to (from tzdata.zi 'L target link' lines)."""
    links: dict[str, str] = {}
    try:
        for line in (zoneinfo / "tzdata.zi").read_text(encoding="utf-8").splitlines():
            if line.startswith("L "):
                parts = line.split()
                if len(parts) == 3:
                    links[parts[2]] = parts[1]
    except OSError:
        pass
    return links


def canonical_zone(zone: str, zoneinfo: Path = ZONEINFO) -> str:
    zone = re.sub(r"^(posix|right)/", "", zone.strip())
    links = zone_links(zoneinfo)
    seen = set()
    while zone in links and zone not in seen:
        seen.add(zone)
        zone = links[zone]
    return zone


def zone_coords(zone: str, zoneinfo: Path = ZONEINFO) -> Optional[tuple[float, float]]:
    """Approximate position of a time zone's main city, or None (UTC, Etc/GMT+3 ... have no place)."""
    table = zone_table(zoneinfo)
    for z in (zone.strip(), canonical_zone(zone, zoneinfo)):
        if z in table:
            return parse_iso6709(table[z][1])
    return None


def zone_exists(zone: str, zoneinfo: Path = ZONEINFO) -> bool:
    if not zone or not ZONE_RE.match(zone) or ".." in zone:
        return False
    if zone in ("UTC", "Etc/UTC") or zone in zone_table(zoneinfo) or zone in zone_links(zoneinfo):
        return True
    return (zoneinfo / zone).is_file() and not zone.endswith(".tab")


def current_zone(localtime: str = "/etc/localtime", timezone_file: str = "/etc/timezone") -> str:
    """The system's zone, from where /etc/localtime points (what timedatectl reports too)."""
    try:
        target = os.readlink(localtime)
        m = re.search(r"zoneinfo/(.+)$", target)
        if m:
            return re.sub(r"^(posix|right)/", "", m[1])
    except OSError:
        pass
    try:
        z = Path(timezone_file).read_text(encoding="utf-8").strip()
        if z:
            return z
    except OSError:
        pass
    return "UTC"


def country_names(zoneinfo: Path = ZONEINFO) -> dict[str, str]:
    names: dict[str, str] = {}
    try:
        for line in (zoneinfo / "iso3166.tab").read_text(encoding="utf-8").splitlines():
            if line and not line.startswith("#") and "\t" in line:
                cc, name = line.split("\t", 1)
                names[cc] = name.strip()
    except OSError:
        pass
    return names


def locale_country(lang: str) -> Optional[str]:
    """'en_GB.UTF-8' -> 'GB'. C, POSIX and C.UTF-8 have no country."""
    m = re.match(r"^[a-z]{2,3}_([A-Z]{2})\b", lang or "")
    return m[1] if m else None


def zones_for_country(cc: str, zoneinfo: Path = ZONEINFO) -> list[str]:
    """Zones whose main country is cc (in the tz database's own geographic order). zone1970.tab lists
    several countries per zone; the first one is the most populous, so it decides."""
    cc = (cc or "").upper()
    primary, secondary = [], []
    for name in ("zone1970.tab", "zone.tab"):
        for cols in _tab_rows(name, zoneinfo):
            codes = cols[0].split(",")
            if codes[0] == cc and cols[2] not in primary:
                primary.append(cols[2])
            elif cc in codes[1:] and cols[2] not in secondary:
                secondary.append(cols[2])
    return primary + [z for z in secondary if z not in primary]


def chooser_lines(first_country: Optional[str] = None, zoneinfo: Path = ZONEINFO) -> list[str]:
    """'Europe/London  ·  United Kingdom' for every zone; the given country's zones first, then UTC, then A-Z."""
    table = zone_table(zoneinfo)
    names = country_names(zoneinfo)

    def line(z: str) -> str:
        codes = table.get(z, ([], ""))[0]
        country = names.get(codes[0], codes[0]) if codes else ""
        return f"{z}  ·  {country}" if country else z

    first = zones_for_country(first_country, zoneinfo) if first_country else []
    rest = sorted(z for z in table if z not in first)
    return [line(z) for z in first] + ["UTC  ·  Coordinated Universal Time"] + [line(z) for z in rest]


def parse_geoip(xml: str, zoneinfo: Path = ZONEINFO) -> Optional[str]:
    """Ubuntu's geoip answer (<Response>...<TimeZone>Europe/London</TimeZone>...) -> the zone, if it's real."""
    m = re.search(r"<TimeZone>\s*([^<\s]+)\s*</TimeZone>", xml or "")
    if not m:
        return None
    zone = m[1]
    return zone if zone_exists(zone, zoneinfo) else None


# =====================================================================  secret filter
class SecretRules:
    """Secret Shield's patterns (the daemon's secret_shield_rules.json), applied the same way the daemon does:
    regex + capture group, placeholders skipped, an entropy floor and a digit-and-letter check where a rule asks."""

    def __init__(self, data: dict):
        self.rules = []
        for r in data.get("rules", []):
            try:
                rx = re.compile(r["regex"], re.I if "i" in r.get("flags", "") else 0)
            except re.error:
                continue
            self.rules.append((r["id"], rx, int(r.get("group", 0)), float(r.get("entropy", 0)),
                               bool(r.get("needsDigitAndLetter"))))
        self.placeholders = {p.lower() for p in data.get("placeholders", [])}

    @classmethod
    def load(cls, path: Optional[str] = None) -> "SecretRules":
        for p in ([path] if path else []) + [c for c in RULES_CANDIDATES if c]:
            try:
                return cls(json.loads(Path(p).read_text(encoding="utf-8")))
            except (OSError, ValueError):
                continue
        return cls({"rules": FALLBACK_RULES})

    def _placeholder(self, s: str) -> bool:
        s = s.strip().strip("\"'")
        low = s.lower()
        if low in self.placeholders or re.fullmatch(r"(x+|\*+|\.+|_+|-+|0+)", low):
            return True
        return any(t in s for t in ("${", "{{", "%(", "<", ">", "$("))

    def match(self, text: str) -> Optional[str]:
        """The id of the first rule the text trips, or None."""
        for line in text.splitlines() or [text]:
            line = line[:4000]
            for rid, rx, group, entropy, digit_letter in self.rules:
                for m in rx.finditer(line):
                    g = group if group <= (rx.groups or 0) else 0
                    secret = m.group(g)
                    if not secret or self._placeholder(secret):
                        continue
                    if entropy and shannon(secret) < entropy:
                        continue
                    if digit_letter and not (re.search(r"\d", secret) and re.search(r"[A-Za-z]", secret)):
                        continue
                    return rid
        return None


# Used only when the daemon's rules file is missing (a stripped-down install): the most common key shapes.
FALLBACK_RULES = [
    {"id": "private-key", "regex": "-----BEGIN (?:RSA |EC |DSA |OPENSSH |PGP |ENCRYPTED )?PRIVATE KEY"},
    {"id": "api-key", "regex": "\\b(?:sk-[A-Za-z0-9_-]{20,}|gh[pousr]_[A-Za-z0-9]{36,}|AKIA[0-9A-Z]{16})"},
]


def shannon(s: str) -> float:
    if not s:
        return 0.0
    counts = {c: s.count(c) for c in set(s)}
    return -sum(n / len(s) * math.log2(n / len(s)) for n in counts.values())


# =====================================================================  command line
def main(argv: list[str]) -> int:
    cmd = argv[1] if len(argv) > 1 else "help"
    arg = argv[2] if len(argv) > 2 else ""
    if cmd == "coords":
        c = zone_coords(arg or current_zone())
        if not c:
            return 1
        print(f"{c[0]:.2f} {c[1]:.2f}")
        return 0
    if cmd == "current":
        print(current_zone())
        return 0
    if cmd == "zones":
        print("\n".join(chooser_lines(arg or locale_country(os.environ.get("LANG", "")))))
        return 0
    if cmd == "suggest":
        cc = locale_country(arg or os.environ.get("LC_TIME") or os.environ.get("LANG", ""))
        zones = zones_for_country(cc) if cc else []
        if zones:
            print("\n".join(zones))
        return 0 if zones else 1
    if cmd == "valid":
        return 0 if zone_exists(arg) else 1
    if cmd == "geoip":
        z = parse_geoip(sys.stdin.read(65536))
        if z:
            print(z)
        return 0 if z else 1
    if cmd == "secret":
        data = sys.stdin.buffer.read(1_000_000)
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            return 1                      # images and other binary copies: nothing to read
        rid = SecretRules.load().match(text)
        if rid:
            print(rid)
        return 0 if rid else 1
    print(__doc__)
    return 2 if cmd != "help" else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
