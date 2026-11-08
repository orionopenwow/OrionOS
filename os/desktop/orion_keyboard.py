"""OrionOS keyboard layout (H13): chosen at first start and in Settings, never a hard-coded US layout.

The layout lives in ~/.config/orion/sway.d/keyboard.conf (Sway reads it at every start; Settings and orion-keyboard
also apply it at once with swaymsg). The names come from the system's own list (/usr/share/X11/xkb/rules/evdev.lst).
Pure functions here; tested with a trimmed copy of that list.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

LST = Path("/usr/share/X11/xkb/rules/evdev.lst")
CODE_RE = re.compile(r"^[a-z][a-z0-9_-]{0,15}$")
VARIANT_RE = re.compile(r"^[A-Za-z0-9_-]{0,31}$")

# A first guess from the time zone's country: the layout most keyboards sold there have.
COUNTRY_LAYOUT = {
    "GB": "gb", "IE": "ie", "US": "us", "CA": "us", "AU": "us", "NZ": "us", "DE": "de", "AT": "at", "CH": "ch",
    "FR": "fr", "BE": "be", "NL": "us", "LU": "fr", "ES": "es", "PT": "pt", "IT": "it", "SE": "se", "NO": "no",
    "DK": "dk", "FI": "fi", "IS": "is", "PL": "pl", "CZ": "cz", "SK": "sk", "HU": "hu", "RO": "ro", "BG": "bg",
    "GR": "gr", "TR": "tr", "RU": "ru", "UA": "ua", "JP": "jp", "KR": "kr", "CN": "cn", "TW": "tw", "BR": "br",
    "MX": "latam", "AR": "latam", "CL": "latam", "CO": "latam", "PE": "latam", "IN": "in", "IL": "il",
    "SI": "si", "HR": "hr", "RS": "rs", "EE": "ee", "LV": "lv", "LT": "lt", "ZA": "us",
}
# Shown first in the first-start list (the rest are a type-to-search away).
COMMON = ["us", "gb", "de", "fr", "es", "it", "pt", "br", "latam", "ch", "se", "no", "dk", "fi", "pl", "nl",
          "be", "ie", "ca", "jp"]


def parse_lst(text: str) -> dict[str, list]:
    """evdev.lst -> {"layouts": [(code, name)], "variants": [(layout, variant, name)]}."""
    section = ""
    layouts: list[tuple[str, str]] = []
    variants: list[tuple[str, str, str]] = []
    for line in text.splitlines():
        if line.startswith("!"):
            section = line[1:].strip()
            continue
        m = re.match(r"^\s+(\S+)\s+(.+?)\s*$", line)
        if not m:
            continue
        if section == "layout":
            layouts.append((m[1], m[2]))
        elif section == "variant":
            v = re.match(r"^(\S+):\s*(.+)$", m[2])
            if v:
                variants.append((v[1], m[1], v[2]))
    return {"layouts": layouts, "variants": variants}


def load(path: Path = LST) -> dict[str, list]:
    try:
        return parse_lst(path.read_text(encoding="utf-8", errors="replace"))
    except OSError:
        return {"layouts": [("us", "English (US)"), ("gb", "English (UK)")], "variants": []}


def choices(data: dict[str, list], with_variants: bool = True) -> list[tuple[str, str]]:
    """[(value, name)] for a picker, sorted by name. value is 'layout' or 'layout:variant'."""
    out = [(code, name) for code, name in data["layouts"]]
    if with_variants:
        out += [(f"{lay}:{var}", name) for lay, var, name in data["variants"]]
    return sorted(out, key=lambda x: x[1].lower())


def common_choices(data: dict[str, list]) -> list[tuple[str, str]]:
    names = dict(data["layouts"])
    return [(c, names[c]) for c in COMMON if c in names]


def name_of(data: dict[str, list], layout: str, variant: str = "") -> str:
    if variant:
        for lay, var, name in data["variants"]:
            if lay == layout and var == variant:
                return name
    return dict(data["layouts"]).get(layout, layout)


def split(value: str) -> tuple[str, str]:
    lay, _, var = (value or "").partition(":")
    return lay.strip(), var.strip()


def valid(data: dict[str, list], layout: str, variant: str = "") -> bool:
    if not CODE_RE.match(layout or "") or not VARIANT_RE.match(variant or ""):
        return False
    if layout not in dict(data["layouts"]):
        return False
    return not variant or any(lay == layout and var == variant for lay, var, _ in data["variants"])


def suggest(country: Optional[str], data: Optional[dict[str, list]] = None) -> str:
    code = COUNTRY_LAYOUT.get((country or "").upper(), "us")
    if data is not None and code not in dict(data["layouts"]):
        return "us"
    return code


def render_conf(layout: str, variant: str = "") -> str:
    lines = ["# Orion: your keyboard layout (first start or Settings > Keyboard & windows; orion-keyboard set)",
             f"input type:keyboard xkb_layout {layout}"]
    lines.append(f"input type:keyboard xkb_variant {variant}" if variant else 'input type:keyboard xkb_variant ""')
    return "\n".join(lines) + "\n"


def parse_conf(text: Optional[str]) -> Optional[tuple[str, str]]:
    if not text:
        return None
    lay = re.search(r"^input\s+\S+\s+xkb_layout\s+(\S+)", text, re.M)
    var = re.search(r'^input\s+\S+\s+xkb_variant\s+"?([^"\s]*)"?', text, re.M)
    if not lay:
        return None
    return lay[1], (var[1] if var else "")


def sway_commands(layout: str, variant: str = "") -> list[str]:
    return [f"input type:keyboard xkb_layout {layout}",
            f"input type:keyboard xkb_variant {variant}" if variant else 'input type:keyboard xkb_variant ""']


def active_from_inputs(inputs: list) -> Optional[str]:
    """The layout name Sway reports for the first real keyboard (swaymsg -t get_inputs)."""
    for i in inputs or []:
        if isinstance(i, dict) and i.get("type") == "keyboard" and i.get("xkb_active_layout_name"):
            if "virtual" in (i.get("name") or "").lower():
                continue
            return i["xkb_active_layout_name"]
    return None
