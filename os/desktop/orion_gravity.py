"""Gravity mode and the optional Hyprland session (after-1.0 plan §3, Phase 4): the logic behind orion-gravity.

Gravity mode bends the real desktop around the black hole with a Hyprland screen shader (hyprland/gravity.frag).
Decisions from the plans: it is OFF by default, it is switched off by itself on battery and while reduced motion is on,
and it needs the Hyprland session (Sway/SwayFX, the default, can't run screen shaders). Plain data in, data out:
tested without a screen (os/desktop/tests/test_gravity.py).

Files (all in the user's home):
  ~/.config/orion/gravity          "on" or "off" (what you chose; missing = off)
  ~/.config/orion/session.env      ORION_COMPOSITOR=hyprland|sway (which session the next login starts; missing = sway)
  ~/.config/orion/hypr.d/gravity.conf   what Hyprland reads at start (the shader line, or nothing)
  ~/.config/orion/hypr.d/keyboard.conf  your keyboard layout in Hyprland's words
  ~/.cache/orion/gravity.frag      the shader with this screen's shape filled in
"""
from __future__ import annotations

import re
from typing import Callable, Iterable, Optional

SHADER_SRC = "/opt/orion/desktop/hyprland/gravity.frag"
ON_WORDS = ("on", "1", "yes", "true")


def wanted(text: Optional[str]) -> bool:
    """Your choice, read back from ~/.config/orion/gravity (no file = off, the default)."""
    return (text or "").strip().lower() in ON_WORDS


def on_battery(supplies: Iterable[tuple[str, dict[str, str]]]) -> bool:
    """[(name, {"type", "status", "online"})] from /sys/class/power_supply: True when running on battery."""
    have_ac = False
    ac_online = False
    discharging = False
    for _name, f in supplies:
        kind = (f.get("type") or "").strip()
        if kind == "Mains":
            have_ac = True
            ac_online = ac_online or (f.get("online") or "").strip() == "1"
        elif kind == "Battery" and (f.get("status") or "").strip() == "Discharging":
            discharging = True
    if have_ac:
        return not ac_online      # a charger is known: on battery exactly when it's unplugged
    return discharging


def reduced_motion(gsettings_animations: Optional[str], gtk_settings: Optional[str], login_conf: Optional[str]) -> bool:
    """Reduced motion is on if GNOME's animations are off (what the HUD's prefers-reduced-motion follows), the GTK
    settings say so, or the login screen's reduced-motion choice is on (orion-ctl login reduced-motion on)."""
    if (gsettings_animations or "").strip().lower() == "false":
        return True
    if gtk_settings and re.search(r"^\s*gtk-enable-animations\s*=\s*(false|0)\s*$", gtk_settings, re.M | re.I):
        return True
    if login_conf and re.search(r"^\s*REDUCED_MOTION\s*=\s*\"?(1|yes|true|on)\"?\s*$", login_conf, re.M | re.I):
        return True
    return False


def effective(want: bool, compositor: str, battery: bool, reduced: bool) -> tuple[bool, str]:
    """(shader on?, plain reason) from your choice and the situation."""
    if not want:
        return False, "Gravity mode is off."
    if compositor != "hyprland":
        return False, ("Gravity mode is on and will bend windows in the Hyprland session. "
                       "This session can't bend other apps' windows (Sway has no screen shaders).")
    if reduced:
        return False, "Gravity mode is paused while reduced motion is on."
    if battery:
        return False, "Gravity mode is paused on battery. It comes back when you plug in."
    return True, "Gravity mode is on: windows near the core bend toward it."


def shader_for(template: str, width: int, height: int) -> str:
    """The shader with this screen's shape (width / height) filled in."""
    aspect = (width / height) if width > 0 and height > 0 else 16 / 9
    return re.sub(r"const float ASPECT = [0-9.]+;", f"const float ASPECT = {aspect:.4f};", template, count=1)


def gravity_conf(path: Optional[str]) -> str:
    head = "# Orion Gravity mode (orion-gravity on|off; Settings > Keyboard & windows). Written by orion-gravity.\n"
    return head + (f"decoration:screen_shader = {path}\n" if path else "# off\n")


def keyboard_conf(layout: str, variant: str = "") -> str:
    layout = re.sub(r"[^A-Za-z0-9_,-]", "", layout or "us") or "us"
    variant = re.sub(r"[^A-Za-z0-9_,-]", "", variant or "")
    return ("# Orion: your keyboard layout (orion-keyboard set; Settings > Keyboard & windows)\n"
            f"input:kb_layout = {layout}\ninput:kb_variant = {variant}\n")


def session_choice(env_text: Optional[str]) -> str:
    """Which session the next login starts, from ~/.config/orion/session.env (default sway)."""
    m = None
    for m in re.finditer(r"^\s*(?:export\s+)?ORION_COMPOSITOR=\"?([A-Za-z]+)\"?\s*(?:#.*)?$", env_text or "", re.M):
        pass
    v = m[1].lower() if m else "sway"
    return v if v in ("sway", "hyprland") else "sway"


def set_session(env_text: Optional[str], choice: str) -> str:
    """session.env with ORION_COMPOSITOR set to `choice`, everything else kept as you wrote it."""
    if choice not in ("sway", "hyprland"):
        raise ValueError(choice)
    lines = [ln for ln in (env_text or "").splitlines()
             if not re.match(r"^\s*(?:export\s+)?ORION_COMPOSITOR=", ln)]
    lines.append(f"ORION_COMPOSITOR={choice}   # orion-gravity session sway|hyprland")
    return "\n".join(lines).lstrip("\n") + "\n"


def read_supplies(listdir: Callable[[str], list[str]], read: Callable[[str], Optional[str]],
                  base: str = "/sys/class/power_supply") -> list[tuple[str, dict[str, str]]]:
    out = []
    for name in sorted(listdir(base)):
        f = {k: (read(f"{base}/{name}/{k}") or "") for k in ("type", "status", "online")}
        out.append((name, f))
    return out
