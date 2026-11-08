#!/usr/bin/env python3
"""OrionOS hardware report card (cheap feature 2): will this PC be happy with OrionOS?

Run it from the USB stick ("Try Orion") before installing. It looks at the graphics card and its driver, Wi-Fi,
Bluetooth (and Bluetooth audio), speakers and microphone, the webcam, sleep, battery health, screen scaling, the
touchpad and NVIDIA's driver, and answers in plain words: a green "Safe to install on this PC", or a short red list
of what doesn't work yet, each with its fix. orion-check-hardware prints it; Settings > This PC's hardware and the
HUD show it; Orion can send it to your phone as a card.

Everything is read from /sys, /proc and a few commands (wpctl, swaymsg, orion-gpu-env) through a Probe, so tests run
on sample trees without any of that hardware.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable, Optional

VENDORS = {"0x8086": "Intel", "0x1002": "AMD", "0x10de": "NVIDIA", "0x1af4": "Virtual", "0x80ee": "VirtualBox",
           "0x15ad": "VMware", "0x1234": "QEMU", "0x1b36": "QEMU"}
REAL_DRIVERS = {"i915", "xe", "amdgpu", "radeon", "nouveau", "nvidia"}
VM_DRIVERS = {"virtio_gpu", "virtio-pci", "vboxvideo", "vmwgfx", "qxl", "bochs-drm", "bochs", "cirrus", "cirrus-qemu"}
BASIC_DRIVERS = {"simpledrm", "efifb", "vesafb", "simplefb", ""}
ORDER = ["gpu", "nvidia", "wifi", "bluetooth", "speakers", "mic", "webcam", "suspend", "battery", "scaling", "touchpad"]


class Probe:
    """Where the facts come from. root prefixes /sys and /proc and /usr (tests); run executes a command."""

    def __init__(self, root: str = "", run: Optional[Callable[[list[str]], tuple[int, str]]] = None,
                 env: Optional[dict[str, str]] = None) -> None:
        self.root = root
        self._run = run or _run
        self.env = env if env is not None else dict(os.environ)

    def p(self, path: str) -> Path:
        return Path(self.root + path)

    def read(self, path: str) -> Optional[str]:
        try:
            return self.p(path).read_text(encoding="utf-8", errors="replace").strip()
        except OSError:
            return None

    def glob(self, pattern: str) -> list[Path]:
        base = Path(self.root or "/")
        return sorted(base.glob(pattern.lstrip("/")))

    def link_name(self, path: Path) -> str:
        try:
            return os.path.basename(os.readlink(path))
        except OSError:
            return ""

    def run(self, argv: list[str]) -> tuple[int, str]:
        return self._run(argv)


def _run(argv: list[str]) -> tuple[int, str]:
    try:
        r = subprocess.run(argv, capture_output=True, text=True, timeout=8)
        return r.returncode, r.stdout
    except (OSError, subprocess.TimeoutExpired):
        return 127, ""


def row(id_: str, label: str, status: str, text: str, fix: str = "") -> dict[str, str]:
    """status: ok | warn | fail | na (not on this PC)."""
    return {"id": id_, "label": label, "status": status, "text": text, "fix": fix}


# ------------------------------------------------------------------ facts
def in_vm(pr: Probe) -> bool:
    if pr.env.get("ORION_HWCHECK_VM") in ("0", "1"):
        return pr.env["ORION_HWCHECK_VM"] == "1"
    rc, out = pr.run(["systemd-detect-virt"])
    return rc == 0 and out.strip() not in ("", "none")


def gpu_cards(pr: Probe) -> list[dict[str, str]]:
    cards = []
    for c in pr.glob("/sys/class/drm/card*"):
        if "-" in c.name or not re.fullmatch(r"card\d+", c.name):
            continue
        drv = pr.link_name(c / "device" / "driver")
        vendor = pr.read(f"/sys/class/drm/{c.name}/device/vendor") or ""
        cards.append({"card": c.name, "driver": drv, "vendor": VENDORS.get(vendor, vendor or "Unknown")})
    return cards


def pci_devices(pr: Probe) -> list[dict[str, str]]:
    out = []
    for d in pr.glob("/sys/bus/pci/devices/*"):
        base = f"/sys/bus/pci/devices/{d.name}"
        out.append({"slot": d.name, "class": pr.read(base + "/class") or "", "vendor": pr.read(base + "/vendor") or "",
                    "driver": pr.link_name(d / "driver")})
    return out


def module_loaded(pr: Probe, name: str) -> bool:
    mods = pr.read("/proc/modules") or ""
    return any(line.split(" ", 1)[0] == name for line in mods.splitlines())


def battery(pr: Probe) -> Optional[dict[str, Any]]:
    for b in pr.glob("/sys/class/power_supply/BAT*"):
        base = f"/sys/class/power_supply/{b.name}"
        for full, design in (("energy_full", "energy_full_design"), ("charge_full", "charge_full_design")):
            f, d = pr.read(f"{base}/{full}"), pr.read(f"{base}/{design}")
            if f and d and f.isdigit() and d.isdigit() and int(d) > 0:
                return {"name": b.name, "health": min(100, round(100 * int(f) / int(d))),
                        "cycles": int(pr.read(f"{base}/cycle_count") or 0) or None}
        return {"name": b.name, "health": None, "cycles": None}
    return None


def parse_wpctl(text: str) -> dict[str, list[str]]:
    """`wpctl status` -> {"sinks": [...], "sources": [...]} (Audio section only, names without volumes)."""
    out: dict[str, list[str]] = {"sinks": [], "sources": []}
    section, which = "", None
    for line in text.splitlines():
        if re.match(r"^\S", line):
            section = line.strip()
            which = None
            continue
        if section != "Audio":
            continue
        m = re.search(r"[├└]─\s*([\w ]+):", line)
        if m:
            which = {"Sinks": "sinks", "Sources": "sources"}.get(m[1].strip())
            continue
        if which:
            m = re.search(r"(?:\*\s*)?\d+\.\s+(.+?)(?:\s+\[.*\])?\s*$", line)
            if m:
                out[which].append(m[1].strip())
    return out


def asound_cards(pr: Probe) -> list[str]:
    text = pr.read("/proc/asound/cards") or ""
    return [m[1].strip() for m in re.finditer(r"^\s*\d+\s+\[[^\]]*\]:\s*[^-]*-\s*(.+)$", text, re.M)]


def suspend_modes(pr: Probe) -> tuple[bool, str]:
    state = (pr.read("/sys/power/state") or "").split()
    mem = (pr.read("/sys/power/mem_sleep") or "")
    cur = re.search(r"\[(\w+)\]", mem)
    return "mem" in state, (cur[1] if cur else "")


def touchpad(pr: Probe, sway_inputs: Optional[list]) -> Optional[str]:
    for i in sway_inputs or []:
        if isinstance(i, dict) and i.get("type") == "touchpad":
            return i.get("name") or "Touchpad"
    for m in re.finditer(r'^N: Name="([^"]+)"', pr.read("/proc/bus/input/devices") or "", re.M):
        if re.search(r"(?i)touchpad|trackpad|glidepoint", m[1]):
            return m[1]
    return None


def sway_json(pr: Probe, what: str) -> Optional[Any]:
    if not pr.env.get("SWAYSOCK") and not pr.env.get("ORION_HWCHECK_SWAY"):
        return None
    rc, out = pr.run(["swaymsg", "-r", "-t", what])
    if rc != 0:
        return None
    try:
        return json.loads(out)
    except ValueError:
        return None


# ------------------------------------------------------------------ checks
def check_gpu(pr: Probe, vm: bool) -> dict[str, str]:
    cards = gpu_cards(pr)
    real = [c for c in cards if c["driver"] in REAL_DRIVERS]
    if real:
        names = ", ".join(f"{c['vendor']} ({c['driver']})" for c in real)
        return row("gpu", "Graphics", "ok", f"{names}: full-speed graphics.")
    if any(c["driver"] in VM_DRIVERS for c in cards) or vm:
        return row("gpu", "Graphics", "na", "Virtual machine graphics (software drawing). Real PCs use their own card.")
    return row("gpu", "Graphics", "fail", "Only the basic screen driver is running: everything will be slow and "
               "the glass effects stay off.", "NVIDIA card: use the NVIDIA version of OrionOS. Others: check for a "
               "firmware (BIOS) update, or try the newest OrionOS stick.")


def check_nvidia(pr: Probe) -> dict[str, str]:
    has = any(c["vendor"] == "NVIDIA" for c in gpu_cards(pr)) or \
        any(d["vendor"] == "0x10de" and d["class"].startswith("0x03") for d in pci_devices(pr))
    if not has:
        return row("nvidia", "NVIDIA", "na", "No NVIDIA card in this PC.")
    if module_loaded(pr, "nvidia"):
        ver = pr.read("/sys/module/nvidia/version") or ""
        if (pr.read("/sys/module/nvidia_drm/parameters/modeset") or "N").upper().startswith("Y"):
            return row("nvidia", "NVIDIA", "ok", f"NVIDIA's driver {ver} is running, ready for the desktop.".replace("  ", " "))
        return row("nvidia", "NVIDIA", "fail", "NVIDIA's driver is loaded, but without screen control (mode "
                   "setting), so the desktop can't use it.", "Add nvidia-drm.modeset=1 to the boot options (the "
                   "NVIDIA stick and the installer do this), then restart.")
    if module_loaded(pr, "nouveau"):
        return row("nvidia", "NVIDIA", "warn", "The free nouveau driver is running: it works, but games and video "
                   "are slow.", "For full speed use the NVIDIA version of OrionOS; installing on the disk adds "
                   "NVIDIA's driver by itself (needs internet).")
    return row("nvidia", "NVIDIA", "fail", "NVIDIA card without a driver.",
               "Use the NVIDIA version of OrionOS, or after installing run: sudo ubuntu-drivers install")


def check_wifi(pr: Probe, vm: bool) -> dict[str, str]:
    for n in pr.glob("/sys/class/net/*"):
        if (n / "wireless").exists() or (n / "phy80211").exists():
            drv = pr.link_name(n / "device" / "driver")
            for rf in pr.glob("/sys/class/rfkill/rfkill*"):
                base = f"/sys/class/rfkill/{rf.name}"
                if pr.read(base + "/type") == "wlan" and pr.read(base + "/hard") == "1":
                    return row("wifi", "Wi-Fi", "warn", "Wi-Fi works but is switched off by a key or switch.",
                               "Press the airplane-mode key (often Fn + a key with an antenna), or a switch on the side.")
            return row("wifi", "Wi-Fi", "ok", f"Wi-Fi adapter found ({n.name}{', ' + drv if drv else ''}).")
    if vm:
        return row("wifi", "Wi-Fi", "na", "Virtual machine: it uses the host's connection.")
    if any(d["class"].startswith("0x0280") and not d["driver"] for d in pci_devices(pr)):
        return row("wifi", "Wi-Fi", "fail", "There is a Wi-Fi chip, but no driver for it.",
                   "Some chips (often Broadcom) need a driver from the internet: plug in a network cable, then run "
                   "sudo ubuntu-drivers install. A small USB Wi-Fi adapter also works.")
    return row("wifi", "Wi-Fi", "warn", "No Wi-Fi found.", "Use a network cable, or a USB Wi-Fi adapter.")


def check_bluetooth(pr: Probe, vm: bool) -> dict[str, str]:
    if pr.glob("/sys/class/bluetooth/hci*"):
        if not pr.glob("/usr/lib/*/spa-0.2/bluez5"):
            return row("bluetooth", "Bluetooth", "warn", "Bluetooth works, but Bluetooth headphones and speakers "
                       "won't play sound yet.", "sudo apt install libspa-0.2-bluetooth (newer OrionOS sticks have it).")
        return row("bluetooth", "Bluetooth", "ok", "Bluetooth works, including headphones and speakers.")
    if vm:
        return row("bluetooth", "Bluetooth", "na", "Virtual machine: no Bluetooth.")
    return row("bluetooth", "Bluetooth", "warn", "No Bluetooth found (or it's switched off in the firmware settings).",
               "If this PC has Bluetooth, turn it on in the firmware (BIOS) settings.")


def check_audio(pr: Probe, vm: bool) -> list[dict[str, str]]:
    rc, out = pr.run(["wpctl", "status"])
    nodes = parse_wpctl(out) if rc == 0 else None
    cards = asound_cards(pr)
    fix_fw = ("Most laptops since 2019 need Intel's sound firmware: sudo apt install firmware-sof-signed "
              "alsa-ucm-conf, then restart (newer OrionOS sticks have it).")
    rows = []
    if nodes is None:
        if cards:
            rows.append(row("speakers", "Speakers", "warn", f"Sound chip found ({cards[0]}), but the sound service "
                            "isn't answering, so it couldn't be tested.", "Log in to the Orion desktop and run the check again."))
        else:
            rows.append(row("speakers", "Speakers", "na" if vm else "fail", "No sound device found.",
                            "" if vm else fix_fw))
        rows.append(row("mic", "Microphone", "na", "Not checked (no sound service)."))
        return rows
    sinks = [s for s in nodes["sinks"] if not re.search(r"(?i)dummy|auto_null", s)]
    if sinks:
        rows.append(row("speakers", "Speakers", "ok", f"Sound out: {sinks[0]}."))
    elif cards:
        rows.append(row("speakers", "Speakers", "fail", "There is a sound chip, but no sound comes out.", fix_fw))
    else:
        rows.append(row("speakers", "Speakers", "na" if vm else "fail", "No speakers or headphone socket found.",
                        "" if vm else fix_fw))
    if nodes["sources"]:
        rows.append(row("mic", "Microphone", "ok", f"Microphone: {nodes['sources'][0]}."))
    elif sinks and not vm:
        rows.append(row("mic", "Microphone", "warn", "No microphone found. Talking to Orion needs one.",
                        "Plug in a headset, or install the sound firmware: " + fix_fw.split(": ", 1)[1]))
    else:
        rows.append(row("mic", "Microphone", "na" if vm else "fail", "No microphone found.", "" if vm else fix_fw))
    return rows


def check_webcam(pr: Probe, vm: bool, laptop: bool) -> dict[str, str]:
    names = []
    for v in pr.glob("/sys/class/video4linux/video*"):
        n = pr.read(f"/sys/class/video4linux/{v.name}/name")
        if n and n not in names and not re.search(r"(?i)metadata", n):
            names.append(n)
    if names:
        return row("webcam", "Camera", "ok", f"Camera found ({names[0]}). It stays off until you turn it on in "
                   "Settings > Privacy.")
    if laptop and not vm:
        return row("webcam", "Camera", "warn", "No camera found. Some newer laptop cameras (Intel IPU6) don't "
                   "work on Linux yet.", "A USB webcam works for video calls.")
    return row("webcam", "Camera", "na", "No camera.")


def check_suspend(pr: Probe, vm: bool) -> dict[str, str]:
    if vm:
        return row("suspend", "Sleep", "na", "Virtual machine: the host decides.")
    can, mode = suspend_modes(pr)
    if not can:
        return row("suspend", "Sleep", "fail", "This PC can't go to sleep with Linux.",
                   "Look for a firmware (BIOS) update; some have a 'Linux' sleep setting.")
    if mode == "s2idle":
        return row("suspend", "Sleep", "ok", "Sleep works (light sleep: the battery drains a little faster than "
                   "in Windows' deepest sleep).")
    return row("suspend", "Sleep", "ok", "Sleep works.")


def check_battery(pr: Probe) -> dict[str, str]:
    b = battery(pr)
    if not b:
        return row("battery", "Battery", "na", "No battery (a desktop PC).")
    if b["health"] is None:
        return row("battery", "Battery", "ok", "Battery found (its wear can't be read).")
    cyc = f", {b['cycles']} charge cycles" if b.get("cycles") else ""
    if b["health"] >= 80:
        return row("battery", "Battery", "ok", f"Battery health {b['health']}%{cyc}.")
    return row("battery", "Battery", "warn", f"Battery health {b['health']}%{cyc}: it holds noticeably less than "
               "when new. That's the battery, not OrionOS.", "A replacement battery brings the time back.")


def check_scaling(pr: Probe, outputs: Optional[list], suggest: Optional[Callable[[dict], float]]) -> dict[str, str]:
    active = [o for o in outputs or [] if isinstance(o, dict) and o.get("active")]
    if not active:
        return row("scaling", "Screen size", "na", "Not checked (no desktop running).")
    parts, off = [], []
    for o in active:
        sc = float(o.get("scale") or 1)
        name = "Built-in screen" if re.match(r"^(eDP|LVDS|DSI)", o.get("name", "")) else (o.get("model") or o.get("name"))
        parts.append(f"{name} at {round(sc * 100)}%")
        if suggest:
            want = suggest(o)
            if abs(want - sc) >= 0.24:
                off.append((name, want))
    if off:
        n, w = off[0]
        return row("scaling", "Screen size", "warn", "; ".join(parts) + ".",
                   f"Text may look {'small' if w > 1 else 'big'}: try {round(w * 100)}% for {n} in Settings > Displays.")
    return row("scaling", "Screen size", "ok", "; ".join(parts) + ".")


def check_touchpad(pr: Probe, inputs: Optional[list], laptop: bool, vm: bool) -> dict[str, str]:
    t = touchpad(pr, inputs)
    if t:
        return row("touchpad", "Touchpad", "ok", f"Touchpad works ({t}). Tap to click and two-finger scrolling are on.")
    if laptop and not vm:
        return row("touchpad", "Touchpad", "fail", "This laptop's touchpad wasn't found.",
                   "Check for a firmware (BIOS) update; a USB mouse works meanwhile.")
    return row("touchpad", "Touchpad", "na", "No touchpad (fine for a desktop PC).")


# ------------------------------------------------------------------ the report
def report(pr: Optional[Probe] = None, suggest: Optional[Callable[[dict], float]] = None) -> dict[str, Any]:
    pr = pr or Probe()
    vm = in_vm(pr)
    laptop = battery(pr) is not None or bool(pr.glob("/sys/class/drm/card*-eDP-*"))
    outputs = sway_json(pr, "get_outputs")
    inputs = sway_json(pr, "get_inputs")
    rows = [check_gpu(pr, vm), check_nvidia(pr), check_wifi(pr, vm), check_bluetooth(pr, vm), *check_audio(pr, vm),
            check_webcam(pr, vm, laptop), check_suspend(pr, vm), check_battery(pr),
            check_scaling(pr, outputs, suggest), check_touchpad(pr, inputs, laptop, vm)]
    rows.sort(key=lambda r: ORDER.index(r["id"]) if r["id"] in ORDER else 99)
    fails = [r for r in rows if r["status"] == "fail"]
    warns = [r for r in rows if r["status"] == "warn"]
    cmdline = pr.read("/proc/cmdline") or ""
    if fails:
        headline = f"Not ready yet: {len(fails)} thing{'s' if len(fails) != 1 else ''} to fix before installing"
    elif vm:
        headline = "Fine in this virtual machine (run the check on the real PC before installing)"
    else:
        headline = "Safe to install on this PC"
    return {"safe": not fails, "headline": headline, "vm": vm, "laptop": laptop,
            "try_mode": "orion.try=1" in cmdline, "usb": "orion.slot=" in cmdline,
            "fails": len(fails), "warnings": len(warns), "rows": rows}


def card_text(rep: dict[str, Any]) -> tuple[str, str]:
    """(title, body) for the phone card and the notification."""
    if rep["safe"]:
        lines = [f"{r['label']}: {r['text']}" for r in rep["rows"] if r["status"] == "warn"]
        body = "Everything important works." + ("\nWorth knowing:\n- " + "\n- ".join(lines) if lines else "")
        return rep["headline"], body
    lines = [f"{r['label']}: {r['text']} Fix: {r['fix']}" for r in rep["rows"] if r["status"] == "fail"]
    return rep["headline"], "\n".join(f"- {x}" for x in lines)


def default_suggest() -> Optional[Callable[[dict], float]]:
    try:
        here = Path(__file__).resolve().parent
        if str(here) not in sys.path:
            sys.path.insert(0, str(here))
        import orion_hardware as oh  # type: ignore
    except Exception:
        return None
    return lambda o: float(oh.choose_scale(o, oh.sysfs_size_mm(o.get("name", ""))))
