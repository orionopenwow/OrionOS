#!/usr/bin/env python3
"""OrionOS displays and printing: the logic behind orion-displays and orion-printers (os/hardware/README.md).

Everything here works offline and is plain data in, data out, so it can be tested without a screen or printer:
  * screens: a stable key for each monitor (make, model, serial), a sensible first layout for a new set of
    screens (laptop panel left, external screens to the right, native modes, a text size from the pixel
    density, in Windows-like steps: 1, 1.25, 1.5, 1.75, 2), the kanshi profiles file written from Orion's own store, the layout you arranged read back from
    `swaymsg -t get_outputs`, the primary screen and which screen each workspace lives on;
  * printers: `lpstat` output read into a list (always run with LC_ALL=C), and the paper size for the
    time zone's country (Letter in the US, Canada, Mexico and a few more; A4 everywhere else).

The time zone -> country lookup is the OS basics one (os/basics/orion_basics.py), reused when it's there.
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import Iterable, Optional

INTERNAL_RE = re.compile(r"^(eDP|LVDS|DSI)(-|$)", re.I)       # a laptop's own panel
NAME_RE = re.compile(r"[^A-Za-z0-9_-]+")
# Letter-size countries: the US and its territories, Canada, Mexico, the Philippines and most of Central America,
# Chile, Colombia and Venezuela (the same list glibc's LC_PAPER uses). Everyone else uses A4.
LETTER_COUNTRIES = frozenset(
    "US PR AS GU MP VI UM CA MX PH CL CO VE CR DO GT HN NI PA SV BZ".split())


# =====================================================================  screens
def output_desc(o: dict) -> str:
    """'Make Model Serial', the form kanshi and sway both match on (missing parts are 'Unknown')."""
    return " ".join(str(o.get(k) or "Unknown") for k in ("make", "model", "serial"))


def is_internal(o: dict) -> bool:
    return bool(INTERNAL_RE.match(o.get("name", "")))


def output_keys(outputs: list[dict]) -> dict[str, str]:
    """connector name -> key. The key is 'Make Model Serial', so a monitor is recognised on any port. Screens
    that can't be told apart that way (no make and model, or two identical ones without serial numbers) are
    keyed by their connector name instead."""
    descs = [output_desc(o) for o in outputs]
    keys = {}
    for o, d in zip(outputs, descs):
        unknown = o.get("make") in (None, "", "Unknown") and o.get("model") in (None, "", "Unknown")
        keys[o["name"]] = o["name"] if unknown or descs.count(d) > 1 else d
    return keys


def mode_str(m: Optional[dict]) -> Optional[str]:
    """{'width':1920,'height':1080,'refresh':60000} -> '1920x1080@60.000Hz' (sway reports mHz; 0 = unknown)."""
    if not m or not m.get("width"):
        return None
    s = f"{m['width']}x{m['height']}"
    if m.get("refresh"):
        s += f"@{m['refresh'] / 1000:.3f}Hz"
    return s


def parse_mode(s: Optional[str]) -> Optional[tuple[int, int]]:
    m = re.match(r"^(\d+)x(\d+)", s or "")
    return (int(m[1]), int(m[2])) if m else None


def native_mode(o: dict) -> Optional[dict]:
    """The mode a screen runs best at. Sway turns new screens on in their preferred mode, so the current mode is
    used while the screen is on; otherwise the biggest mode, at its highest refresh rate."""
    if o.get("active") and o.get("current_mode", {}).get("width"):
        return o["current_mode"]
    modes = o.get("modes") or []
    if not modes:
        return o.get("current_mode")
    return max(modes, key=lambda m: (m["width"] * m["height"], m.get("refresh", 0)))


def edid_size_mm(edid: bytes) -> Optional[tuple[int, int]]:
    """Physical image size from an EDID block: the first detailed timing (millimetres) or bytes 21-22 (cm)."""
    if len(edid) < 128 or edid[:8] != b"\x00\xff\xff\xff\xff\xff\xff\x00":
        return None
    d = edid[54:72]
    if d[0] or d[1]:                                   # a detailed timing descriptor, not a text block
        w = d[12] | ((d[14] & 0xF0) << 4)
        h = d[13] | ((d[14] & 0x0F) << 8)
        if w > 0 and h > 0:
            return (w, h)
    if edid[21] and edid[22]:
        return (edid[21] * 10, edid[22] * 10)
    return None


def sysfs_size_mm(name: str, drm: Path = Path("/sys/class/drm")) -> Optional[tuple[int, int]]:
    """Look up a connector's EDID (/sys/class/drm/card0-eDP-1/edid) and read the screen's size from it."""
    try:
        for p in drm.glob(f"card*-{name}/edid"):
            size = edid_size_mm(p.read_bytes())
            if size:
                return size
    except OSError:
        pass
    return None


# Fractional steps like Windows' 100/125/150/175/200% (and close to the sizes Windows picks by itself): the pixels
# per inch at or above which each step is used.
SCALE_STEPS = [(2.0, 215), (1.75, 190), (1.5, 155), (1.25, 125)]
MIN_LOGICAL_WIDTH = 1280


def tidy_scale(s: float):
    """2.0 -> 2, 1.5 -> 1.5 (kanshi and sway take both; whole numbers stay whole in the files)."""
    return int(s) if float(s).is_integer() else round(float(s), 4)


def choose_scale(o: dict, size_mm: Optional[tuple[int, int]]):
    """The text size for a screen: 1, 1.25, 1.5, 1.75 or 2 from its pixel density, never so big that the screen is
    less than 1280 (logical) pixels wide. Without a known size, only laptop panels are guessed (2 from 3000 pixels
    wide, 1.5 from 2400). Fractional sizes are sharp for Wayland apps; older X11 (Xwayland) apps look a little soft
    at them, which is why Settings says so next to the choice."""
    m = native_mode(o) or {}
    w = m.get("width") or 0
    if size_mm and size_mm[0] > 0 and w:
        dpi = w / (size_mm[0] / 25.4)
        for scale, min_dpi in SCALE_STEPS:
            if dpi >= min_dpi and w / scale >= MIN_LOGICAL_WIDTH:
                return tidy_scale(scale)
        return 1
    if is_internal(o):
        if w >= 3000:
            return 2
        if w >= 2400:
            return tidy_scale(1.5)
    return 1


def auto_profile(outputs: list[dict], sizes: Optional[dict[str, tuple[int, int]]] = None,
                 name: Optional[str] = None) -> dict:
    """A first layout for a set of screens nobody has arranged yet: the laptop panel on the left, the other
    screens to its right in connector order (DP-1 before DP-2), tops lined up, each at its native mode."""
    sizes = sizes or {}
    keys = output_keys(outputs)
    order = sorted(outputs, key=lambda o: (not is_internal(o), natural_key(o["name"])))
    x, outs = 0, []
    for o in order:
        mode = native_mode(o)
        scale = choose_scale(o, sizes.get(o["name"]))
        outs.append({"criteria": keys[o["name"]], "enable": True, "mode": mode_str(mode),
                     "position": [x, 0], "scale": scale, "transform": "normal"})
        x += int((mode or {}).get("width", 1920) / scale)
    return {"name": name or profile_name_for(outputs), "auto": True, "outputs": outs}


def natural_key(s: str) -> list:
    return [int(p) if p.isdigit() else p.lower() for p in re.split(r"(\d+)", s)]


def profile_name_for(outputs: list[dict]) -> str:
    """'laptop', 'laptop-dell-u2720q', 'dell-u2720q-lg-27ul850'... (unique names are made later)."""
    parts = []
    for o in sorted(outputs, key=lambda o: (not is_internal(o), natural_key(o["name"]))):
        if is_internal(o):
            parts.append("laptop")
        elif o.get("model") not in (None, "", "Unknown"):
            parts.append(o["model"])
        else:
            parts.append(o["name"])
    return safe_name("-".join(parts) or "screens")


def safe_name(s: str) -> str:
    s = NAME_RE.sub("-", s.strip()).strip("-").lower()
    return s[:60] or "screens"


def layout_from_outputs(outputs: list[dict], name: str) -> dict:
    """The layout on screen now (after Arrange or by hand) as a profile: positions, modes, scale, rotation, and
    which screens are switched off."""
    keys = output_keys(outputs)
    outs = []
    for o in sorted(outputs, key=lambda o: (not o.get("active"), o.get("rect", {}).get("x", 0), natural_key(o["name"]))):
        if o.get("active"):
            r = o.get("rect", {})
            sc = o.get("scale", 1)
            outs.append({"criteria": keys[o["name"]], "enable": True, "mode": mode_str(o.get("current_mode")),
                         "position": [r.get("x", 0), r.get("y", 0)],
                         "scale": int(sc) if float(sc).is_integer() else round(float(sc), 4),
                         "transform": o.get("transform") or "normal"})
        else:
            outs.append({"criteria": keys[o["name"]], "enable": False})
    return {"name": safe_name(name), "auto": False, "outputs": outs}


def combo(profile: dict) -> tuple[str, ...]:
    return tuple(sorted(o["criteria"] for o in profile["outputs"]))


def current_combo(outputs: list[dict]) -> tuple[str, ...]:
    return tuple(sorted(output_keys(outputs).values()))


def find_profile(store: dict, outputs: list[dict]) -> Optional[dict]:
    """The profile for exactly the screens plugged in now (kanshi uses the first one that matches)."""
    c = current_combo(outputs)
    for p in store.get("profiles", []):
        if combo(p) == c:
            return p
    return None


def unique_name(store: dict, name: str, ignore: Optional[dict] = None) -> str:
    taken = {p["name"] for p in store.get("profiles", []) if p is not ignore}
    n, i = name, 2
    while n in taken:
        n, i = f"{name}-{i}", i + 1
    return n


def add_profile(store: dict, profile: dict) -> dict:
    """Put a profile in the store. A profile for the same set of screens is replaced (so a saved layout wins over
    the automatic one); saved layouts are listed before automatic ones."""
    profs = [p for p in store.get("profiles", []) if combo(p) != combo(profile) and p["name"] != profile["name"]]
    profs.append(profile)
    profs.sort(key=lambda p: bool(p.get("auto")))
    store["profiles"] = profs
    return store


def quote(s: str) -> str:
    return '"' + s.replace('"', "") + '"' if re.search(r'[\s"]', s) else s


def render_kanshi(store: dict, hook: str = "orion-displays changed") -> str:
    lines = ["# Written by orion-displays from ~/.config/orion/displays.json: change screens with Settings > Displays,",
             "# Arrange (orion-displays arrange) or orion-displays save NAME. Hand edits here are replaced.", ""]
    for p in store.get("profiles", []):
        lines.append(f"profile {p['name']} {{")
        for o in p["outputs"]:
            parts = [f"\toutput {quote(o['criteria'])}"]
            if not o.get("enable", True):
                parts.append("disable")
            else:
                parts.append("enable")
                if o.get("mode"):
                    parts.append(f"mode {o['mode']}")
                if o.get("position") is not None:
                    parts.append(f"position {o['position'][0]},{o['position'][1]}")
                if o.get("scale") is not None:
                    parts.append(f"scale {o['scale']}")
                if o.get("transform") and o["transform"] != "normal":
                    parts.append(f"transform {o['transform']}")
            lines.append(" ".join(parts))
        if hook:
            lines.append(f"\texec {hook}")
        lines.append("}")
        lines.append("")
    return "\n".join(lines)


def sway_commands(profile: dict, outputs: list[dict]) -> list[str]:
    """swaymsg commands that put a stored profile on screen now (orion-displays apply)."""
    by_key = {v: k for k, v in output_keys(outputs).items()}
    cmds = []
    for o in profile["outputs"]:
        name = by_key.get(o["criteria"])
        if not name:
            raise KeyError(o["criteria"])
        if not o.get("enable", True):
            cmds.append(f"output {name} disable")
            continue
        c = f"output {name} enable"
        if o.get("mode"):
            c += f" mode {o['mode']}"
        if o.get("position") is not None:
            c += f" position {o['position'][0]} {o['position'][1]}"
        if o.get("scale") is not None:
            c += f" scale {o['scale']}"
        c += f" transform {o.get('transform') or 'normal'}"
        cmds.append(c)
    # switch screens off last, so there is always one on
    return sorted(cmds, key=lambda c: c.endswith(" disable"))


def primary_output(outputs: list[dict], preferred: Optional[str] = None) -> Optional[str]:
    """The screen the Orion HUD (workspace 1) lives on: the one you picked (Settings > Displays, by key) when it's
    on; else the laptop's own panel when it's on; else the screen furthest left."""
    active = [o for o in outputs if o.get("active")]
    if not active:
        return None
    keys = output_keys(outputs)
    for o in active:
        if preferred and preferred in (keys[o["name"]], o["name"]):
            return o["name"]
    for o in active:
        if is_internal(o):
            return o["name"]
    return min(active, key=lambda o: (o.get("rect", {}).get("x", 0), o.get("rect", {}).get("y", 0)))["name"]


def workspace_plan(outputs: list[dict], primary: Optional[str]) -> dict[int, str]:
    """Workspace number -> screen. 1 (the HUD) is always on the primary screen. With one other screen, your
    apps' first workspaces 2-5 go there (so the HUD stays in view while you work) and 6-9 stay on the primary.
    With more screens, 2-9 are shared out in order from left to right; with one screen everything is on it."""
    active = [o for o in outputs if o.get("active")]
    if not primary or not active:
        return {}
    others = [o["name"] for o in sorted(active, key=lambda o: (o.get("rect", {}).get("x", 0), o.get("rect", {}).get("y", 0)))
              if o["name"] != primary]
    plan = {1: primary}
    if not others:
        plan.update({n: primary for n in range(2, 10)})
    elif len(others) == 1:
        plan.update({n: others[0] for n in range(2, 6)})
        plan.update({n: primary for n in range(6, 10)})
    else:
        per = 8 / len(others)
        for i, n in enumerate(range(2, 10)):
            plan[n] = others[min(int(i / per), len(others) - 1)]
    return plan


def lid_closed(root: Path = Path("/proc/acpi/button/lid")) -> bool:
    try:
        return any("closed" in p.read_text() for p in root.glob("*/state"))
    except OSError:
        return False


def outputs_summary(outputs: list[dict], store: dict) -> dict:
    """What `orion-displays list --json` prints (the Settings app reads it)."""
    keys = output_keys(outputs)
    prim = primary_output(outputs, store.get("primary"))
    cur = find_profile(store, outputs)
    plan = workspace_plan(outputs, prim)
    outs = []
    for o in outputs:
        r = o.get("rect", {})
        outs.append({
            "name": o["name"], "key": keys[o["name"]], "make": o.get("make"), "model": o.get("model"),
            "serial": o.get("serial"), "internal": is_internal(o), "active": bool(o.get("active")),
            "primary": o["name"] == prim, "mode": mode_str(o.get("current_mode")) if o.get("active") else None,
            "position": [r.get("x", 0), r.get("y", 0)] if o.get("active") else None,
            "scale": o.get("scale") if o.get("active") else None, "transform": o.get("transform"),
            "modes": sorted({mode_str(m) for m in (o.get("modes") or []) if mode_str(m)},
                            key=lambda s: tuple(-x for x in parse_mode(s) or (0, 0))),
            "workspaces": sorted(n for n, out in plan.items() if out == o["name"]),
        })
    return {"outputs": outs, "primary": prim, "current_profile": cur["name"] if cur else None,
            "profiles": [{"name": p["name"], "auto": bool(p.get("auto")), "current": p is cur,
                          "outputs": [o["criteria"] for o in p["outputs"]]} for p in store.get("profiles", [])]}


# =====================================================================  printers
def parse_lpstat(long_p: str = "", devices: str = "", default: str = "", discovered: str = "") -> dict:
    """Read LC_ALL=C output of `lpstat -l -p`, `lpstat -v`, `lpstat -d` and `lpstat -e` into
    {"default": name|None, "printers": [{name, state, enabled, description, location, uri, default, added}]}.
    'added' is false for printers CUPS found on the network but nobody added (driverless, ready to use)."""
    printers: dict[str, dict] = {}
    cur = None
    after_header = False
    for line in long_p.splitlines():
        m = re.match(r"^printer (\S+) (?:is (idle|now printing \S+?)|(disabled))\.?\s", line + " ")
        if m:
            state = "idle" if m[2] == "idle" else "printing" if m[2] else "disabled"
            cur = printers.setdefault(m[1], {"name": m[1]})
            cur.update({"state": state, "enabled": state != "disabled", "added": True})
            if state == "printing":
                cur["job"] = m[2].split()[-1]
            after_header = True
            continue
        m = re.match(r"^\s+(Description|Location):\s?(.*)$", line)
        if m and cur is not None:
            cur[m[1].lower()] = m[2].strip()
        elif cur is not None and after_header and state_reason(line):
            cur["reason"] = state_reason(line)     # the line right under the header: why it stopped / what it does
        after_header = False
    for line in devices.splitlines():
        m = re.match(r"^device for (\S+?):\s*(\S+)", line)
        if m:
            printers.setdefault(m[1], {"name": m[1], "added": True})["uri"] = m[2]
    dflt = None
    m = re.search(r"system default destination:\s*(\S+)", default)
    if m:
        dflt = m[1]
    for line in discovered.splitlines():
        n = line.strip()
        if n and n not in printers and not n.startswith("lpstat:"):
            printers[n] = {"name": n, "state": "idle", "enabled": True, "added": False}
    out = []
    for p in printers.values():
        p.setdefault("state", "unknown")
        p.setdefault("enabled", p["state"] != "disabled")
        p.setdefault("description", p["name"].replace("_", " "))
        p.setdefault("location", "")
        p.setdefault("uri", None)
        p["default"] = p["name"] == dflt
        out.append(p)
    out.sort(key=lambda p: (not p["default"], not p["added"], p["name"].lower()))
    return {"default": dflt, "printers": out}


def state_reason(line: str) -> Optional[str]:
    """The indented line under a printer's header ("\\tOut of labels", "\\treason unknown")."""
    s = line.strip()
    if not line.startswith(("\t", " ")) or not s or ":" in s:
        return None
    return s


def _basics():
    for d in (os.environ.get("ORION_BASICS_DIR", ""), "/opt/orion/basics",
              str(Path(__file__).resolve().parents[1] / "basics")):
        if d and (Path(d) / "orion_basics.py").is_file():
            if d not in sys.path:
                sys.path.insert(0, d)
            import orion_basics  # type: ignore
            return orion_basics
    return None


def zone_country(zone: str, zoneinfo: Optional[Path] = None) -> Optional[str]:
    """'America/Toronto' -> 'CA' from the tz database (OS basics' zone table). UTC and Etc/* -> None."""
    ob = _basics()
    if ob is None:                                    # OS basics not installed: read the tz tables directly
        zi = zoneinfo or Path("/usr/share/zoneinfo")
        for tab in ("zone1970.tab", "zone.tab"):
            try:
                for line in (zi / tab).read_text(encoding="utf-8").splitlines():
                    cols = line.split("\t")
                    if not line.startswith("#") and len(cols) >= 3 and cols[2] == zone.strip():
                        return cols[0].split(",")[0]
            except OSError:
                continue
        return None
    zi = zoneinfo or ob.ZONEINFO
    table = ob.zone_table(zi)
    for z in (zone.strip(), ob.canonical_zone(zone, zi)):
        if z in table and table[z][0]:
            return table[z][0][0]
    return None


def paper_for_country(cc: Optional[str]) -> str:
    return "letter" if cc and cc.upper() in LETTER_COUNTRIES else "a4"


def paper_for_zone(zone: str, zoneinfo: Optional[Path] = None) -> str:
    return paper_for_country(zone_country(zone, zoneinfo))


def current_zone() -> str:
    ob = _basics()
    if ob is not None:
        return ob.current_zone()
    try:
        return re.sub(r"^.*/zoneinfo/", "", os.readlink("/etc/localtime"))
    except OSError:
        return "UTC"


# =====================================================================  store
def load_store(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("profiles"), list):
            return data
    except (OSError, ValueError):
        pass
    return {"version": 1, "primary": None, "profiles": []}


def save_store(path: Path, store: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(store, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def write_if_changed(path: Path, text: str) -> bool:
    try:
        if path.read_text(encoding="utf-8") == text:
            return False
    except OSError:
        pass
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)
    return True


def iter_json_lines(stream: Iterable[str]) -> Iterable[dict]:
    for line in stream:
        line = line.strip()
        if line:
            try:
                yield json.loads(line)
            except ValueError:
                continue
