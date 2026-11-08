"""Pure parsers for the command-line tools Orion Settings reads (nmcli, wpctl, bluetoothctl, powerprofilesctl,
swaymsg, Orion's own tools). No side effects: each takes text and returns plain data, so the unit tests can feed
captured sample outputs (os/settings/tests/samples)."""
from __future__ import annotations

import json
import re
from typing import Any, Optional

MAC_RE = re.compile(r"^[0-9A-F]{2}(:[0-9A-F]{2}){5}$", re.I)


# ----------------------------------------------------------------------------------------------- nmcli
def nm_split(line: str) -> list[str]:
    """Split one line of `nmcli -t` output: fields are ':'-separated, a literal ':' or '\\' is backslash-escaped."""
    out, cur, esc = [], [], False
    for ch in line:
        if esc:
            cur.append(ch)
            esc = False
        elif ch == "\\":
            esc = True
        elif ch == ":":
            out.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
    out.append("".join(cur))
    return out


def parse_nm_wifi_list(text: str) -> list[dict[str, Any]]:
    """`nmcli -t -f IN-USE,SSID,SIGNAL,SECURITY dev wifi list` -> one entry per network name, strongest first.
    Hidden networks (empty name) are left out; the same name seen on several access points is merged."""
    nets: dict[str, dict[str, Any]] = {}
    for line in text.splitlines():
        if not line.strip():
            continue
        f = nm_split(line)
        if len(f) < 4:
            continue
        in_use, ssid, signal, security = f[0].strip(), f[1], f[2], f[3].strip()
        if not ssid:
            continue
        try:
            sig = int(signal)
        except ValueError:
            sig = 0
        sec = "" if security in ("", "--") else security
        cur = nets.get(ssid)
        entry = {"ssid": ssid, "signal": sig, "security": sec, "in_use": in_use == "*",
                 "enterprise": "802.1X" in sec, "sae": "WPA3" in sec and "WPA2" not in sec}
        if cur is None or sig > cur["signal"]:
            if cur is not None:
                entry["in_use"] = entry["in_use"] or cur["in_use"]
            nets[ssid] = entry
        elif entry["in_use"]:
            cur["in_use"] = True
    return sorted(nets.values(), key=lambda n: (not n["in_use"], -n["signal"], n["ssid"].lower()))


def parse_nm_connections(text: str) -> list[dict[str, str]]:
    """`nmcli -t -f NAME,TYPE,DEVICE connection show` -> [{name, type, device}]"""
    out = []
    for line in text.splitlines():
        f = nm_split(line)
        if len(f) >= 3 and f[0]:
            out.append({"name": f[0], "type": f[1], "device": f[2]})
    return out


def parse_nm_devices(text: str) -> list[dict[str, str]]:
    """`nmcli -t -f DEVICE,TYPE,STATE,CONNECTION device` -> [{device, type, state, connection}] (no loopback)"""
    out = []
    for line in text.splitlines():
        f = nm_split(line)
        if len(f) >= 4 and f[1] != "loopback":
            out.append({"device": f[0], "type": f[1], "state": f[2], "connection": f[3]})
    return out


def wifi_bars(signal: int) -> str:
    """Signal strength in words (plain-words labels; the icon carries the bars)."""
    if signal >= 70:
        return "Strong"
    if signal >= 45:
        return "Good"
    if signal >= 25:
        return "Weak"
    return "Very weak"


# ----------------------------------------------------------------------------------------------- wpctl
_WP_NODE = re.compile(r"^(?P<star>\*)?\s*(?P<id>\d+)\.\s+(?P<name>.+?)\s*(?:\[(?P<attrs>[^\]]*)\])?\s*$")


def parse_wpctl_status(text: str) -> dict[str, list[dict[str, Any]]]:
    """`wpctl status` (WirePlumber 0.4 and 0.5) -> {"sinks": [...], "sources": [...]} for the Audio section.
    Each node: {id, name, default, volume (0..1.5 or None), muted}."""
    res: dict[str, list[dict[str, Any]]] = {"sinks": [], "sources": []}
    section = None
    sub = None
    for raw in text.splitlines():
        if raw and not raw[0].isspace() and raw.strip():
            section = raw.strip()
            sub = None
            continue
        if section != "Audio":
            continue
        line = raw.replace("│", " ").replace("├", " ").replace("└", " ").replace("─", " ")
        s = line.strip()
        if not s:
            continue
        if s.endswith(":"):
            name = s[:-1].strip().lower()
            sub = {"sinks": "sinks", "sources": "sources"}.get(name)
            continue
        if sub is None:
            continue
        m = _WP_NODE.match(s)
        if not m:
            continue
        attrs = m.group("attrs") or ""
        vol = None
        vm = re.search(r"vol:\s*([0-9.]+)", attrs)
        if vm:
            vol = float(vm.group(1))
        res[sub].append({"id": int(m.group("id")), "name": m.group("name").strip(), "default": bool(m.group("star")),
                         "volume": vol, "muted": "MUTED" in attrs})
    return res


def parse_wpctl_volume(text: str) -> tuple[Optional[float], bool]:
    """`wpctl get-volume @DEFAULT_AUDIO_SINK@` -> (0.55, False). (None, False) when there is no device."""
    m = re.search(r"Volume:\s*([0-9.]+)", text)
    return (float(m.group(1)) if m else None, "[MUTED]" in text)


# ----------------------------------------------------------------------------------------------- bluetoothctl
def parse_bt_show(text: str) -> Optional[dict[str, Any]]:
    """`bluetoothctl show` -> {address, name, powered, discovering, pairable} or None without an adapter."""
    if "No default controller" in text or "Controller" not in text:
        return None
    info: dict[str, Any] = {"address": "", "name": "", "powered": False, "discovering": False, "pairable": False}
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("Controller "):
            parts = s.split()
            if len(parts) > 1:
                info["address"] = parts[1]
        elif ":" in s:
            k, _, v = s.partition(":")
            v = v.strip()
            k = k.strip()
            if k == "Alias" or (k == "Name" and not info["name"]):
                info["name"] = v
            elif k == "Powered":
                info["powered"] = v == "yes"
            elif k == "Discovering":
                info["discovering"] = v == "yes"
            elif k == "Pairable":
                info["pairable"] = v == "yes"
    return info


def parse_bt_devices(text: str) -> list[dict[str, str]]:
    """`bluetoothctl devices` -> [{address, name}] (ignores [NEW]/[CHG] chatter lines)."""
    out = []
    for line in text.splitlines():
        line = re.sub(r"\x1b\[[0-9;]*m|\x01|\x02", "", line)
        m = re.match(r"^\s*(?:\[[A-Z]+\]\s*)?Device\s+([0-9A-Fa-f:]{17})\s*(.*)$", line.strip())
        if m:
            out.append({"address": m.group(1).upper(), "name": m.group(2).strip() or m.group(1)})
    return out


def parse_bt_info(text: str) -> dict[str, Any]:
    """`bluetoothctl info MAC` -> {name, icon, paired, trusted, connected, battery}"""
    info: dict[str, Any] = {"name": "", "icon": "", "paired": False, "trusted": False, "connected": False,
                            "battery": None}
    for line in text.splitlines():
        s = line.strip()
        k, sep, v = s.partition(":")
        if not sep:
            continue
        k, v = k.strip(), v.strip()
        if k == "Alias" or (k == "Name" and not info["name"]):
            info["name"] = v
        elif k == "Icon":
            info["icon"] = v
        elif k in ("Paired", "Bonded"):
            info["paired"] = info["paired"] or v == "yes"
        elif k == "Trusted":
            info["trusted"] = v == "yes"
        elif k == "Connected":
            info["connected"] = v == "yes"
        elif k == "Battery Percentage":
            m = re.search(r"\((\d+)\)", v)
            if m:
                info["battery"] = int(m.group(1))
    return info


def bt_kind(icon: str) -> str:
    """bluetoothctl's Icon field in plain words."""
    return {"audio-headset": "Headset", "audio-headphones": "Headphones", "audio-card": "Speaker",
            "input-keyboard": "Keyboard", "input-mouse": "Mouse", "input-gaming": "Game controller",
            "input-tablet": "Drawing tablet", "phone": "Phone", "computer": "Computer"}.get(icon, "Device")


# ----------------------------------------------------------------------------------------------- power
def parse_power_profiles(text: str) -> tuple[list[str], Optional[str]]:
    """`powerprofilesctl list` -> (["performance", "balanced", "power-saver"], "balanced")"""
    names, active = [], None
    for line in text.splitlines():
        m = re.match(r"^(\*)?\s*([a-z][a-z-]*):\s*$", line)
        if m:
            names.append(m.group(2))
            if m.group(1):
                active = m.group(2)
    return names, active


PROFILE_WORDS = {"power-saver": ("Battery saver", "Slower, quieter and cooler; the battery lasts longest"),
                 "balanced": ("Balanced", "Fast when you need it, saves power when you don't"),
                 "performance": ("Full speed", "Fastest; uses more power and runs warmer")}


def parse_logind_dropin(text: str) -> dict[str, str]:
    """Orion's logind drop-in -> {"HandleLidSwitch": "suspend", ...} (only keys in the [Login] section)."""
    out: dict[str, str] = {}
    sect = None
    for line in text.splitlines():
        s = line.strip()
        if not s or s.startswith(("#", ";")):
            continue
        if s.startswith("["):
            sect = s.strip("[]")
            continue
        if sect == "Login" and "=" in s:
            k, _, v = s.partition("=")
            out[k.strip()] = v.strip()
    return out


# ----------------------------------------------------------------------------------------------- sway outputs
def parse_sway_outputs(text: str) -> list[dict[str, Any]]:
    """`swaymsg -t get_outputs -r` -> [{name, label, active, scale, width, height, refresh}]"""
    try:
        data = json.loads(text or "[]")
    except ValueError:
        return []
    out = []
    for o in data if isinstance(data, list) else []:
        mode = o.get("current_mode") or {}
        label = " ".join(x for x in (o.get("make"), o.get("model")) if x and x != "Unknown").strip()
        out.append({"name": o.get("name", "?"), "label": label or o.get("name", "Screen"),
                    "active": bool(o.get("active", True)), "scale": float(o.get("scale") or 1.0),
                    "width": mode.get("width"), "height": mode.get("height"),
                    "refresh": round((mode.get("refresh") or 0) / 1000) or None})
    return out


# ----------------------------------------------------------------------------------------------- Orion tools
def parse_timezone_status(text: str) -> dict[str, Any]:
    """`orion-timezone status` -> {zone, clock, ntp, synced, rtc}"""
    info: dict[str, Any] = {"zone": "", "clock": "", "ntp": None, "synced": None, "rtc": ""}
    for line in text.splitlines():
        m = re.match(r"^Time zone:\s*(\S+)\s*(?:\((.*)\))?", line)
        if m:
            info["zone"], info["clock"] = m.group(1), (m.group(2) or "")
        elif line.startswith("Network time:"):
            info["ntp"] = line.split(":", 1)[1].strip() == "on"
        elif line.startswith("In sync:"):
            info["synced"] = line.split(":", 1)[1].strip() == "yes"
        elif line.startswith("Hardware clock:"):
            info["rtc"] = line.split(":", 1)[1].strip()
    return info


def parse_zone_lines(text: str) -> list[tuple[str, str]]:
    """orion_basics.py zones ('Europe/London  ·  United Kingdom') -> [("Europe/London", "United Kingdom")]"""
    out = []
    for line in text.splitlines():
        if not line.strip():
            continue
        zone, _, country = line.partition("  ·  ")
        out.append((zone.strip(), country.strip()))
    return out


def parse_nightlight(text: str) -> str:
    """`orion-nightlight status` -> "on" | "off" | "unavailable" | "waiting" """
    t = text.strip()
    if t.startswith("Night light: off"):
        return "off"
    if "not available" in t:
        return "unavailable"
    if "not running yet" in t:
        return "waiting"
    if t.startswith("Night light: on"):
        return "on"
    return "off"


def parse_login_status(text: str) -> dict[str, bool]:
    """`orion-ctl login status` -> {password: bool, reduced_motion: bool}"""
    return {"password": "asks for your password" in text, "reduced_motion": "simple fade" in text}


def parse_env_file(text: str) -> dict[str, str]:
    """KEY=value lines (daemon.env, session.env). Quotes are removed; comments and blank lines skipped."""
    out: dict[str, str] = {}
    for line in text.splitlines():
        s = line.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        if s.startswith("export "):
            s = s[7:].strip()
        k, _, v = s.partition("=")
        v = v.strip()
        if len(v) >= 2 and v[0] == v[-1] and v[0] in "'\"":
            v = v[1:-1]
        out[k.strip()] = v
    return out


def set_env_line(text: str, key: str, value: Optional[str]) -> str:
    """Return the env file text with KEY set to value (None removes it). Other lines are kept exactly."""
    if not re.fullmatch(r"[A-Z][A-Z0-9_]*", key):
        raise ValueError("bad key")
    if value is not None and re.search(r"[\n\r]", value):
        raise ValueError("bad value")
    lines = text.splitlines()
    out, done = [], False
    for line in lines:
        s = line.strip()
        bare = s[7:].lstrip() if s.startswith("export ") else s
        if bare.startswith(key + "=") or bare.startswith(key + " ="):
            if value is not None and not done:
                out.append(f"{key}={value}")
                done = True
            continue
        out.append(line)
    if value is not None and not done:
        out.append(f"{key}={value}")
    return "\n".join(out) + ("\n" if out else "")


def parse_json_maybe(text: str) -> Any:
    try:
        return json.loads(text)
    except ValueError:
        return None
