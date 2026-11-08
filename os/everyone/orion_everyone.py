#!/usr/bin/env python3
"""OrionOS "Everyone can use it" (docs/ORIONOS_COMPLETE_PLAN.md Part 1 and step 6): accessibility, typing other
languages and emoji, more than one person (guest + extra users), laptop power. What each piece does: README.md.

    orion_everyone.py access  status [--json] | session | screen-reader|keyboard|keyboard-touch|contrast on|off|toggle
                              | text-size 1|1.25|1.5|1.75|2 | screen-size 1|1.25|1.5|2|off | hud-zoom N
                              | zoom in|out|reset | wizard
    orion_everyone.py typing  status [--json] | list | add ID | remove ID | emoji
    orion_everyone.py power   status [--json] | watch | once | auto-saver on|off | hibernate on|off
    orion_everyone.py people  status [--json] | check-name LOGIN

Everything that decides something (battery thresholds, power modes, hibernation checks, file writers, the emoji
list, user names) is a plain function with no side effects, so tests run without Sway, fcitx5 or a battery. The
commands around them only read files and run the standard tools (swaymsg, gsettings, notify-send, systemctl).
Nothing here needs root; the root parts are the small shell helpers next to this file.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
import unicodedata
from pathlib import Path
from typing import Any, Iterable, Optional

HOME = Path(os.path.expanduser("~"))
CONF = Path(os.environ.get("ORION_CONF_DIR", str(HOME / ".config" / "orion")))
SYSROOT = os.environ.get("ORION_SYSROOT", "")          # tests point /sys, /proc and /etc reads elsewhere

# =================================================================================================== small helpers


def read(path: str | Path) -> Optional[str]:
    try:
        return Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def write_if_changed(path: Path, text: str, mode: Optional[int] = None) -> bool:
    """Write atomically; True when the content changed."""
    if read(path) == text:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".new")
    tmp.write_text(text, encoding="utf-8")
    if mode is not None:
        os.chmod(tmp, mode)
    os.replace(tmp, path)
    return True


def load_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(read(path) or "{}")
        return data if isinstance(data, dict) else {}
    except ValueError:
        return {}


def run(argv: list[str], timeout: float = 10) -> tuple[int, str]:
    try:
        p = subprocess.run(argv, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=timeout)
        return p.returncode, p.stdout
    except (OSError, subprocess.TimeoutExpired):
        return 127, ""


def spawn(argv: list[str]) -> bool:
    try:
        subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         start_new_session=True)
        return True
    except OSError:
        return False


def orca_bus_up() -> None:
    """[v1gaps] Start the accessibility bus before Orca (os/desktop/orion_a11y.py; orion-a11y-check bus-up)."""
    if shutil.which("orion-a11y-check"):
        run(["orion-a11y-check", "bus-up"], timeout=10)


def notify(title: str, body: str = "", urgency: str = "normal") -> None:
    run(["notify-send", "-a", "Orion", "-u", urgency, title, body], timeout=5)


def set_env_lines(text: str, values: dict[str, Optional[str]]) -> str:
    """Set (or with None remove) KEY=value lines in a shell env file, leaving every other line alone."""
    lines = text.splitlines()
    for key, val in values.items():
        lines = [ln for ln in lines if not re.match(rf"^\s*(export\s+)?{re.escape(key)}=", ln)]
        if val is not None:
            lines.append(f"{key}={val}")
    return "\n".join(lines) + ("\n" if lines else "")


# =================================================================================================== accessibility
ACCESS_FILE = CONF / "access.json"
ACCESS_DEFAULTS: dict[str, Any] = {
    "screen_reader": False,     # Orca
    "keyboard": False,          # on-screen keyboard shown at start
    "keyboard_touch": True,     # on touch screens: wvkbd waits hidden, Super+Alt+K (or the toggle) shows it
    "contrast": False,          # high contrast: GTK theme, HUD, terminal, window borders
    "text_scale": 1.0,          # bigger text: GTK, HUD, terminal, cursor
    "screen_scale": None,       # make everything bigger: Sway output scale for every screen (None = leave it)
    "hud_zoom": 1.0,            # the Orion window only
}
TEXT_SCALES = (1.0, 1.25, 1.5, 1.75, 2.0)
SCREEN_SCALES = (1.0, 1.25, 1.5, 1.75, 2.0)
ZOOM_STEP, ZOOM_MAX = 0.25, 4.0
OSK = "wvkbd-mobintl"
THEMES = {"glass": "Orion-Glass", "solid": "Orion-Glass-Solid", "contrast": "Orion-Glass-HighContrast"}
# Sway's own colours (os/lab/sway-config) and the high-contrast set: white/yellow on black, thick borders.
SWAY_NORMAL = [
    "default_border pixel 2", "default_floating_border pixel 2",
    "client.focused          #2E6BFF  #0B0F1E    #FFFFFF  #00E5FF   #2E6BFF",
    "client.focused_inactive #1E2440  #0B0F1E    #8888AA  #1E2440   #1E2440",
    "client.unfocused        #141932  #05070E    #6B7090  #141932   #141932",
    "client.urgent           #FF5C8A  #0B0F1E    #FFFFFF  #FF5C8A   #FF5C8A",
]
SWAY_CONTRAST = [
    "default_border pixel 4", "default_floating_border pixel 4",
    "client.focused          #FFD400  #000000    #FFFFFF  #FFFFFF   #FFD400",
    "client.focused_inactive #FFFFFF  #000000    #FFFFFF  #FFFFFF   #808080",
    "client.unfocused        #808080  #000000    #FFFFFF  #808080   #808080",
    "client.urgent           #FF4040  #000000    #FFFFFF  #FF4040   #FF4040",
]


def nearest(value: Any, choices: Iterable[float], default: float = 1.0) -> float:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return default
    return min(choices, key=lambda c: abs(c - v))


def clean_access(raw: dict[str, Any]) -> dict[str, Any]:
    """Defaults for anything missing, and only sane values for the rest (the file is the user's to edit)."""
    s = dict(ACCESS_DEFAULTS)
    for k in ("screen_reader", "keyboard", "keyboard_touch", "contrast"):
        if isinstance(raw.get(k), bool):
            s[k] = raw[k]
    s["text_scale"] = nearest(raw.get("text_scale", 1.0), TEXT_SCALES)
    ss = raw.get("screen_scale")
    s["screen_scale"] = None if ss in (None, "", "off", False) else nearest(ss, SCREEN_SCALES)
    try:
        s["hud_zoom"] = round(min(3.0, max(0.5, float(raw.get("hud_zoom", 1.0)))), 2)
    except (TypeError, ValueError):
        s["hud_zoom"] = 1.0
    return s


def load_access(path: Path = ACCESS_FILE) -> dict[str, Any]:
    return clean_access(load_json(path))


def save_access(state: dict[str, Any], path: Path = ACCESS_FILE) -> None:
    write_if_changed(path, json.dumps(clean_access(state), indent=1) + "\n")


def render_sway_access(s: dict[str, Any]) -> str:
    """~/.config/orion/sway.d/access.conf (read by Sway at every start; os/lab/sway-config includes sway.d)."""
    out = ["# Orion Settings > Accessibility (orion-access). Written by Orion; change it there."]
    out += SWAY_CONTRAST if s["contrast"] else SWAY_NORMAL
    out.append(f"seat * xcursor_theme Orion {int(round(24 * max(s['text_scale'], 2.0 if s['contrast'] else 1.0)))}")
    if s["screen_scale"]:
        # Every screen. A size picked for one screen in Settings > Displays (displays.conf, read later) still wins.
        out.append(f"output * scale {s['screen_scale']:g}")
    return "\n".join(out) + "\n"


def render_foot_access(s: dict[str, Any]) -> str:
    """~/.config/foot/orion-access.ini, included at the end of foot.ini: font size and high-contrast colours."""
    out = ["# Orion Settings > Accessibility (orion-access). Written by Orion; change it there.", "[main]"]
    size = 10.5 * s["text_scale"]
    out.append(f"font=Martian Mono:size={size:g}, monospace:size={11 * s['text_scale']:g}, "
               f"Noto Color Emoji:size={10 * s['text_scale']:g}")
    if s["contrast"]:
        out += ["[colors]", "alpha=1.0", "background=000000", "foreground=FFFFFF",
                "regular0=000000", "bright0=808080", "regular1=FF6060", "bright1=FF9090",
                "regular2=60FF60", "bright2=A0FFA0", "regular3=FFD400", "bright3=FFE860",
                "regular4=80A0FF", "bright4=B0C8FF", "regular5=FF80FF", "bright5=FFB0FF",
                "regular6=60FFFF", "bright6=B0FFFF", "regular7=FFFFFF", "bright7=FFFFFF",
                "selection-foreground=000000", "selection-background=FFD400", "urls=FFD400",
                "[cursor]", "color=000000 FFD400"]
    return "\n".join(out) + "\n"


def gtk_theme_for(contrast: bool, fx_active: bool, solid_pref: bool) -> str:
    if contrast:
        return THEMES["contrast"]
    return THEMES["glass"] if fx_active and not solid_pref else THEMES["solid"]


def gsettings_for(s: dict[str, Any], theme: str) -> list[list[str]]:
    """The gsettings that GTK 3/4 and libadwaita read on Wayland (through GSettings or the settings portal)."""
    i, a11y = "org.gnome.desktop.interface", "org.gnome.desktop.a11y"
    on = lambda b: "true" if b else "false"  # noqa: E731
    return [
        ["gsettings", "set", i, "text-scaling-factor", f"{s['text_scale']:g}"],
        ["gsettings", "set", i, "gtk-theme", theme],
        ["gsettings", "set", i, "cursor-size", str(int(round(24 * max(s["text_scale"], 2.0 if s["contrast"] else 1.0))))],
        ["gsettings", "set", f"{a11y}.interface", "high-contrast", on(s["contrast"])],
        ["gsettings", "set", f"{a11y}.applications", "screen-reader-enabled", on(s["screen_reader"])],
        ["gsettings", "set", f"{a11y}.applications", "screen-keyboard-enabled", on(s["keyboard"])],
        ["gsettings", "set", i, "toolkit-accessibility", on(s["screen_reader"])],
    ]


def session_env_for_reader(on: bool) -> dict[str, Optional[str]]:
    """Read at the next log-in by orion-session (~/.config/orion/session.env): Qt and Chromium/Electron apps only
    talk to the screen reader when asked to. GTK apps and the Orion window (WebKitGTK) always do."""
    v = "1" if on else None
    return {"QT_ACCESSIBILITY": v, "QT_LINUX_ACCESSIBILITY_ALWAYS_ON": v, "ACCESSIBILITY_ENABLED": v}


def next_zoom(current: float, base: float, direction: str) -> float:
    """Screen zoom (Sway output scale) one step in or out, never below the screen's own size."""
    if direction == "reset":
        return base
    step = ZOOM_STEP if direction == "in" else -ZOOM_STEP
    return round(min(max(base, ZOOM_MAX), max(base, current + step)), 2)


def has_touch(inputs: Any) -> bool:
    return isinstance(inputs, list) and any(isinstance(d, dict) and d.get("type") == "touch" for d in inputs)


def access_words(s: dict[str, Any]) -> list[str]:
    w = []
    if s["screen_reader"]:
        w.append("screen reader")
    if s["keyboard"]:
        w.append("on-screen keyboard")
    if s["contrast"]:
        w.append("high contrast")
    if s["text_scale"] != 1.0:
        w.append(f"text {int(s['text_scale'] * 100)}%")
    if s["screen_scale"]:
        w.append(f"everything {int(s['screen_scale'] * 100)}%")
    return w


class Access:
    """Applies the accessibility choices to this session. Tests use the pure functions above instead."""

    def __init__(self, conf: Path = CONF) -> None:
        self.conf = conf
        self.file = conf / "access.json"
        self.s = load_access(self.file)

    def save(self) -> None:
        save_access(self.s, self.file)

    def fx_active(self) -> bool:
        rt = os.environ.get("XDG_RUNTIME_DIR", "")
        return bool(rt) and any(Path(rt, "orion-sway-fx").glob("*.conf"))

    def solid_pref(self) -> bool:
        return "ORION_SWAYFX=0" in (read(self.conf / "session.env") or "")

    def apply(self, reload_sway: bool = True) -> None:
        s = self.s
        # flag file for os/lab/sway-config's theme line, which runs again at every Sway reload
        flag = self.conf / "high-contrast"
        if s["contrast"]:
            write_if_changed(flag, "on\n")
        elif flag.exists():
            flag.unlink()
        theme = gtk_theme_for(s["contrast"], self.fx_active(), self.solid_pref())
        for argv in gsettings_for(s, theme):
            run(argv, timeout=5)
        foot_changed = write_if_changed(HOME / ".config" / "foot" / "orion-access.ini", render_foot_access(s))
        sway_changed = write_if_changed(self.conf / "sway.d" / "access.conf", render_sway_access(s))
        env = self.conf / "session.env"
        write_if_changed(env, set_env_lines(read(env) or "", session_env_for_reader(s["screen_reader"])))
        if sway_changed and reload_sway and os.environ.get("SWAYSOCK"):
            run(["swaymsg", "reload"], timeout=10)
        _ = foot_changed  # new terminal windows pick it up; open ones keep their look

    # ---- screen reader
    def reader(self, on: bool) -> str:
        self.s["screen_reader"] = on
        self.save()
        self.apply(reload_sway=False)
        if on:
            if not shutil.which("orca"):
                return "The screen reader (Orca) isn't installed on this computer yet."
            run(["pkill", "-x", "orca"])
            orca_bus_up()  # [v1gaps] accessibility bus first, so Orca hears GTK apps and the HUD on Sway
            spawn(["orca", "--replace"])
            return "Screen reader on. Super+Alt+S turns it off. Some apps may need to be opened again."
        run(["pkill", "-x", "orca"])
        return "Screen reader off."

    # ---- on-screen keyboard
    def osk_running(self) -> bool:
        return run(["pgrep", "-x", OSK])[0] == 0

    def osk(self, action: str) -> str:
        if not shutil.which(OSK):
            return "The on-screen keyboard (wvkbd) isn't installed on this computer yet."
        height = str(int(260 * self.s["text_scale"]))
        if action == "toggle":
            if self.osk_running():
                run(["pkill", f"-{int(signal.SIGRTMIN)}", "-x", OSK])   # wvkbd: SIGRTMIN shows or hides it
                return ""
            spawn([OSK, "-L", height])
            return ""
        if action == "on":
            self.s["keyboard"] = True
            self.save()
            if self.osk_running():
                run(["pkill", "-USR2", "-x", OSK])                   # show
            else:
                spawn([OSK, "-L", height])
            return "On-screen keyboard on. Super+Alt+K hides or shows it."
        self.s["keyboard"] = False
        self.save()
        run(["pkill", "-x", OSK])
        return "On-screen keyboard off."

    # ---- screen zoom (Sway output scale of the focused screen; resets at log-out)
    def zoom(self, direction: str) -> str:
        rc, out = run(["swaymsg", "-t", "get_outputs", "-r"])
        try:
            outs = json.loads(out) if rc == 0 else []
        except ValueError:
            outs = []
        o = next((x for x in outs if x.get("focused")), outs[0] if outs else None)
        if not o:
            return "Zoom needs the Orion session (Sway)."
        name, cur = o.get("name", ""), float(o.get("scale") or 1.0)
        rt = Path(os.environ.get("XDG_RUNTIME_DIR", "/tmp"))
        bases = load_json(rt / "orion-zoom.json")
        base = float(bases.get(name, cur))
        new = next_zoom(cur, base, direction)
        if direction == "reset":
            bases.pop(name, None)
        else:
            bases.setdefault(name, base)
        write_if_changed(rt / "orion-zoom.json", json.dumps(bases))
        run(["swaymsg", f"output {name} scale {new:g}"])
        return f"Zoom {int(round(new / base * 100))}%."

    # ---- at log-in (exec from os/lab/sway-config)
    def session(self) -> None:
        self.apply(reload_sway=True)
        if self.s["screen_reader"] and shutil.which("orca"):
            orca_bus_up()  # [v1gaps]
            spawn(["orca", "--replace"])
        if shutil.which(OSK):
            rc, out = run(["swaymsg", "-t", "get_inputs", "-r"])
            try:
                touch = has_touch(json.loads(out)) if rc == 0 else False
            except ValueError:
                touch = False
            if self.s["keyboard"]:
                spawn([OSK, "-L", str(int(260 * self.s["text_scale"]))])
            elif touch and self.s["keyboard_touch"]:
                spawn([OSK, "--hidden", "-L", str(int(260 * self.s["text_scale"]))])
        Typing().session()


# =================================================================================================== typing
TYPING_FILE = CONF / "typing.json"
# id -> (words, how): fcitx5 input methods. "keyboard-xx" are plain keyboard layouts.
LANGUAGES: dict[str, tuple[str, str]] = {
    "keyboard-us": ("English (US)", "layout"),
    "keyboard-gb": ("English (UK)", "layout"),
    "keyboard-de": ("German", "layout"),
    "keyboard-fr": ("French", "layout"),
    "keyboard-es": ("Spanish", "layout"),
    "keyboard-it": ("Italian", "layout"),
    "keyboard-pt": ("Portuguese", "layout"),
    "keyboard-br": ("Portuguese (Brazil)", "layout"),
    "keyboard-nl": ("Dutch", "layout"),
    "keyboard-se": ("Swedish", "layout"),
    "keyboard-pl": ("Polish", "layout"),
    "keyboard-tr": ("Turkish", "layout"),
    "keyboard-ru": ("Russian", "layout"),
    "keyboard-ua": ("Ukrainian", "layout"),
    "keyboard-gr": ("Greek", "layout"),
    "keyboard-il": ("Hebrew", "layout"),
    "keyboard-ara": ("Arabic", "layout"),
    "keyboard-in-eng": ("Indian (English)", "layout"),
    "m17n_hi_inscript2": ("Hindi", "fcitx5-m17n"),
    "m17n_ta_inscript2": ("Tamil", "fcitx5-m17n"),
    "m17n_vi_telex": ("Vietnamese", "fcitx5-m17n"),
    "pinyin": ("Chinese (Pinyin)", "fcitx5-chinese-addons"),
    "mozc": ("Japanese", "fcitx5-mozc"),
    "hangul": ("Korean", "fcitx5-hangul"),
}
IM_ENV = {"GTK_IM_MODULE": "fcitx", "QT_IM_MODULE": "fcitx", "XMODIFIERS": "@im=fcitx", "SDL_IM_MODULE": "fcitx"}


def default_layout(sway_config: str) -> str:
    m = re.search(r"^\s*input\s+\S+\s+xkb_layout\s+([A-Za-z]+)", sway_config or "", re.M)
    return m.group(1) if m else "us"


def clean_languages(raw: Any, layout: str = "us") -> list[str]:
    langs = [x for x in (raw if isinstance(raw, list) else []) if isinstance(x, str) and x in LANGUAGES]
    langs = list(dict.fromkeys(langs))
    if not langs:
        first = f"keyboard-{layout}"
        langs = [first if first in LANGUAGES else "keyboard-us"]
    return langs


def typing_enabled(langs: list[str]) -> bool:
    """fcitx5 runs only once there's a second language: one language is just the keyboard Sway already has."""
    return len(langs) > 1


def render_fcitx_profile(langs: list[str]) -> str:
    first = langs[0]
    layout = first.split("-", 1)[1] if first.startswith("keyboard-") else "us"
    out = ["[Groups/0]", "# Group Name", "Name=Default", "# Layout", f"Default Layout={layout}",
           "# Default Input Method", f"DefaultIM={langs[-1]}", ""]
    for n, lang in enumerate(langs):
        out += [f"[Groups/0/Items/{n}]", "# Name", f"Name={lang}", "# Layout", "Layout=", ""]
    out += ["[GroupOrder]", "0=Default", ""]
    return "\n".join(out)


def missing_engines(langs: list[str], installed: Iterable[str]) -> list[str]:
    have = set(installed)
    need = {LANGUAGES[x][1] for x in langs if LANGUAGES[x][1] != "layout"}
    return sorted(p for p in need if p not in have)


# Emoji for the picker (Super+.): straight from Python's Unicode tables, so nothing is downloaded or bundled.
EMOJI_RANGES = ((0x1F300, 0x1F5FF), (0x1F600, 0x1F64F), (0x1F680, 0x1F6FF), (0x1F900, 0x1F9FF),
                (0x1FA70, 0x1FAFF), (0x2600, 0x26FF), (0x2700, 0x27BF))
SKIP = re.compile(r"SKIN TONE|REGIONAL INDICATOR|VARIATION SELECTOR|COMBINING|TAG ")


def emoji_list() -> list[tuple[str, str]]:
    out = []
    for lo, hi in EMOJI_RANGES:
        for cp in range(lo, hi + 1):
            ch = chr(cp)
            name = unicodedata.name(ch, "")
            if not name or unicodedata.category(ch) != "So" or SKIP.search(name):
                continue
            out.append((ch, name.lower()))
    return out


def emoji_from_line(line: str) -> str:
    """The picker shows "<emoji>  <name>"; give back just the emoji."""
    return line.split("  ", 1)[0].strip()


class Typing:
    def __init__(self, conf: Path = CONF) -> None:
        self.conf = conf
        self.file = conf / "typing.json"
        # the layout chosen at first start / in Settings (orion-keyboard, H13), else the main Sway config
        layout = default_layout((read(HOME / ".config" / "orion" / "sway.d" / "keyboard.conf") or "") + "\n" +
                                (read(HOME / ".config" / "sway" / "config") or ""))
        self.langs = clean_languages(load_json(self.file).get("languages"), layout)

    def installed(self) -> list[str]:
        rc, out = run(["dpkg-query", "-W", "-f", "${Package} ${Status}\n", "fcitx5-m17n", "fcitx5-chinese-addons",
                       "fcitx5-mozc", "fcitx5-hangul"])
        return [ln.split()[0] for ln in out.splitlines() if ln.endswith("installed") and "not-installed" not in ln]

    def status(self) -> dict[str, Any]:
        return {"languages": [{"id": x, "name": LANGUAGES[x][0]} for x in self.langs],
                "enabled": typing_enabled(self.langs), "fcitx5": bool(shutil.which("fcitx5")),
                "missing": missing_engines(self.langs, self.installed()),
                "choices": [{"id": k, "name": v[0]} for k, v in LANGUAGES.items()]}

    def save_apply(self) -> str:
        write_if_changed(self.file, json.dumps({"languages": self.langs}, indent=1) + "\n")
        on = typing_enabled(self.langs)
        env = self.conf / "session.env"
        changed = write_if_changed(env, set_env_lines(read(env) or "", {k: (v if on else None)
                                                                        for k, v in IM_ENV.items()}))
        if on:
            write_if_changed(HOME / ".config" / "fcitx5" / "profile", render_fcitx_profile(self.langs))
            if shutil.which("fcitx5"):
                run(["pkill", "-x", "fcitx5"])
                spawn(["fcitx5", "-d", "--replace"])
        else:
            run(["pkill", "-x", "fcitx5"])
        note = " Log out and back in once so every app sees it." if changed else ""
        return ("Ctrl+Space switches between your languages." if on else "Back to one keyboard.") + note

    def add(self, lang: str) -> str:
        if lang not in LANGUAGES:
            return "That language isn't in the list."
        if lang not in self.langs:
            self.langs.append(lang)
        return f"{LANGUAGES[lang][0]} added. " + self.save_apply()

    def remove(self, lang: str) -> str:
        if lang in self.langs and len(self.langs) > 1:
            self.langs.remove(lang)
        return self.save_apply()

    def session(self) -> None:
        if typing_enabled(self.langs) and shutil.which("fcitx5"):
            spawn(["fcitx5", "-d", "--replace"])

    @staticmethod
    def emoji() -> int:
        lines = "\n".join(f"{e}  {n}" for e, n in emoji_list())
        try:
            p = subprocess.run(["wofi", "--dmenu", "-i", "-p", "Emoji", "--cache-file", "/dev/null"], input=lines,
                               capture_output=True, text=True, timeout=300)
        except (OSError, subprocess.TimeoutExpired):
            return 1
        e = emoji_from_line(p.stdout)
        if p.returncode != 0 or not e:
            return 0
        run(["wl-copy", e])
        time.sleep(0.15)   # let wofi's window close so the text goes to the app underneath
        if run(["wtype", e])[0] != 0:
            notify("Emoji copied", "Paste it with Ctrl+V.")
        return 0


# =================================================================================================== laptop power
POWER_FILE = CONF / "power.json"
WARN, CRITICAL, SLEEP_AT = 15, 7, 4
SAVER_BELOW = 20
REARM = 3            # charge back above a threshold by this much and its warning can show again
SLEEP_GRACE = 180    # seconds after waking (or logging in) before it may put the computer to sleep again
WOKE_GAP = 150       # checks run every 20-60 s; a longer gap means the computer was asleep


def read_batteries(sysroot: str = SYSROOT) -> dict[str, Any]:
    """Batteries and mains power from /sys/class/power_supply."""
    import glob
    bats, ac = [], None
    for d in sorted(glob.glob(f"{sysroot}/sys/class/power_supply/*")):
        typ = (read(f"{d}/type") or "").strip()
        if typ == "Battery" and (read(f"{d}/scope") or "").strip() != "Device":   # not a mouse's battery
            cap = (read(f"{d}/capacity") or "").strip()
            if cap.isdigit():
                bats.append({"name": Path(d).name, "percent": int(cap), "status": (read(f"{d}/status") or "").strip()})
        elif typ == "Mains":
            ac = (read(f"{d}/online") or "").strip() == "1" or bool(ac)
    if not bats:
        return {"present": False, "percent": None, "discharging": False, "on_ac": True if ac is None else ac}
    pct = round(sum(b["percent"] for b in bats) / len(bats))
    discharging = any(b["status"] == "Discharging" for b in bats) or (ac is False)
    return {"present": True, "percent": pct, "discharging": discharging, "on_ac": not discharging, "batteries": bats}


def battery_step(percent: Optional[int], discharging: bool, state: dict[str, Any],
                 now: float) -> tuple[list[tuple[str, ...]], dict[str, Any]]:
    """One check of the battery. Returns what to do (("notify", urgency, title, body) or ("sleep",)) and the new
    state. Each warning shows once per discharge; plugging in (or charging back up) re-arms them."""
    st = {"warned": list(state.get("warned", [])), "slept_at": state.get("slept_at", 0), "last": now}
    acts: list[tuple[str, ...]] = []
    if state.get("last") and now - float(state["last"]) > WOKE_GAP:
        st["slept_at"] = now          # just woke up (a long gap between checks): time to reach the charger
    if percent is None:
        return acts, st
    if not discharging:
        st["warned"] = []
        return acts, st
    st["warned"] = [w for w in st["warned"] if percent <= w + REARM]
    if percent <= SLEEP_AT:
        if now - float(st["slept_at"] or 0) >= SLEEP_GRACE:
            acts.append(("notify", "critical", f"Battery at {percent}%",
                         "Going to sleep now to keep your work safe. Plug in the charger."))
            acts.append(("sleep",))
            st["slept_at"] = now
        return acts, st
    if percent <= CRITICAL and CRITICAL not in st["warned"]:
        acts.append(("notify", "critical", f"Battery very low: {percent}%",
                     f"Plug in the charger now. At {SLEEP_AT}% the computer goes to sleep by itself."))
        st["warned"] += [CRITICAL, WARN]
    elif percent <= WARN and WARN not in st["warned"] and CRITICAL not in st["warned"]:
        acts.append(("notify", "normal", f"Battery low: {percent}%", "Time to find the charger."))
        st["warned"].append(WARN)
    return acts, st


def profile_step(on_ac: bool, percent: Optional[int], active: Optional[str], auto: bool,
                 state: dict[str, Any]) -> tuple[Optional[str], dict[str, Any]]:
    """Power mode linked to the battery: on battery below SAVER_BELOW% switch to power-saver (and performance
    drops to balanced as soon as the charger comes out); plugging in puts back what you had. Returns the mode to
    set (or None) and the new state. A mode you pick yourself is never overridden twice."""
    st = {"restore": state.get("restore"), "saver_on": bool(state.get("saver_on"))}
    if not auto or active is None or percent is None:
        return None, st
    if on_ac:
        if st["restore"] and active != st["restore"]:
            want, st["restore"], st["saver_on"] = st["restore"], None, False
            return want, st
        st["restore"], st["saver_on"] = None, False
        return None, st
    if percent <= SAVER_BELOW and not st["saver_on"] and active != "power-saver":
        st["restore"] = st["restore"] or active
        st["saver_on"] = True
        return "power-saver", st
    if active == "performance" and not st["restore"]:
        st["restore"] = "performance"
        return "balanced", st
    return None, st


def mem_kib(meminfo: str, key: str) -> int:
    m = re.search(rf"^{key}:\s+(\d+)", meminfo or "", re.M)
    return int(m.group(1)) if m else 0


def hibernate_check(meminfo: str, swaps: str, power_state: str, lockdown: str, cmdline: str, resume_conf: bool,
                    profile: str) -> tuple[bool, str]:
    """Can this computer hibernate (save memory to disk and switch off)? Returns (possible, plain reason)."""
    if profile == "image" or "orion.slot" in cmdline:
        return False, ("Off on the USB stick: your memory would have to be written to the stick, outside your "
                       "locked vault, and the stick wears out faster. Sleep works as usual.")
    if profile == "vm":
        return False, "Not used in the virtual machine."
    if "disk" not in (power_state or "").split():
        return False, "This computer doesn't offer hibernation."
    if re.search(r"\[(integrity|confidentiality)\]", lockdown or ""):
        return False, "Secure Boot's lockdown blocks hibernation on this computer."
    ram = mem_kib(meminfo, "MemTotal")
    swap = sum(int(p[2]) for p in (ln.split() for ln in (swaps or "").splitlines()[1:]) if len(p) >= 3
               and p[2].isdigit())
    if swap < ram:
        need = max(1, round(ram / 1024 / 1024 + 0.49))
        have = round(swap / 1024 / 1024, 1)
        return False, f"Needs swap space at least as big as the memory ({need} GB); this computer has {have:g} GB."
    if "resume=" not in (cmdline or "") and not resume_conf:
        return False, "The swap space isn't set up to wake from (no resume setting yet)."
    return True, "Sleeps first, then saves everything to disk and switches off after 2 hours."


def hibernate_possible(sysroot: str = SYSROOT) -> tuple[bool, str]:
    profile = (read(f"{sysroot}/etc/orion/profile") or "").strip()
    return hibernate_check(read(f"{sysroot}/proc/meminfo") or "", read(f"{sysroot}/proc/swaps") or "",
                           read(f"{sysroot}/sys/power/state") or "", read(f"{sysroot}/sys/kernel/security/lockdown")
                           or "", read(f"{sysroot}/proc/cmdline") or "",
                           Path(f"{sysroot}/etc/initramfs-tools/conf.d/resume").exists(), profile)


def clean_power(raw: dict[str, Any]) -> dict[str, Any]:
    return {"auto_saver": raw.get("auto_saver", True) is not False, "hibernate": raw.get("hibernate") is True}


class Power:
    def __init__(self, conf: Path = CONF) -> None:
        self.file = conf / "power.json"
        self.prefs = clean_power(load_json(self.file))
        self.state: dict[str, Any] = {}
        self.pstate: dict[str, Any] = {}

    def save(self) -> None:
        write_if_changed(self.file, json.dumps(self.prefs, indent=1) + "\n")

    def status(self) -> dict[str, Any]:
        ok, why = hibernate_possible()
        return {**read_batteries(), **self.prefs, "hibernate_possible": ok, "hibernate_reason": why,
                "warn_at": WARN, "critical_at": CRITICAL, "sleep_at": SLEEP_AT, "saver_below": SAVER_BELOW}

    def active_profile(self) -> Optional[str]:
        if not shutil.which("powerprofilesctl"):
            return None
        rc, out = run(["powerprofilesctl", "get"], timeout=5)
        return out.strip() if rc == 0 and out.strip() else None

    def sleep(self) -> None:
        verb = "suspend-then-hibernate" if self.prefs["hibernate"] and hibernate_possible()[0] else "suspend"
        run(["systemctl", verb], timeout=30)

    def once(self, now: Optional[float] = None) -> dict[str, Any]:
        b = read_batteries()
        acts, self.state = battery_step(b["percent"], b["discharging"], self.state, now or time.time())
        for a in acts:
            if a[0] == "notify":
                notify(a[2], a[3], a[1])
            elif a[0] == "sleep":
                self.sleep()
        want, self.pstate = profile_step(b["on_ac"], b["percent"], self.active_profile(), self.prefs["auto_saver"],
                                         self.pstate)
        if want:
            run(["powerprofilesctl", "set", want], timeout=5)
        return b

    def watch(self) -> int:
        if not read_batteries()["present"]:
            return 0      # desktop computer: nothing to watch
        self.state = {"slept_at": time.time()}   # never straight to sleep at log-in: time to plug in first
        while True:
            self.prefs = clean_power(load_json(self.file))
            b = self.once()
            time.sleep(20 if (b["percent"] or 100) <= 10 else 60)


# =================================================================================================== people
GUEST = os.environ.get("ORION_GUEST_USER", "guest")
RESERVED = {"root", "orion", "orion-admin", "orion-agent", "guest", "admin", "_greetd", "daemon", "nobody", "sudo",
            "adm", "bin", "sys", "www-data", "ubuntu", "user", "users"}
LOGIN_RE = re.compile(r"^[a-z][a-z0-9-]{1,30}$")


def check_login(name: str, existing: Iterable[str] = ()) -> tuple[bool, str]:
    if not LOGIN_RE.match(name or ""):
        return False, "Use 2 to 31 small letters, numbers or dashes, starting with a letter."
    if name in RESERVED or name.startswith("orion"):
        return False, "That name is kept for OrionOS itself. Pick another."
    if name in set(existing):
        return False, "Someone already has that name."
    return True, ""


def login_from_name(full: str) -> str:
    ascii_ = unicodedata.normalize("NFKD", full or "").encode("ascii", "ignore").decode().lower()
    word = re.sub(r"[^a-z0-9-]", "", (ascii_.split() or [""])[0])
    word = re.sub(r"^[^a-z]+", "", word)[:31]
    return word if len(word) >= 2 else ""


def people_status() -> dict[str, Any]:
    import pwd
    me = pwd.getpwuid(os.getuid()).pw_name
    others = [p.pw_name for p in pwd.getpwall() if 1000 <= p.pw_uid < 60000 and p.pw_name not in
              ("orion-agent", "orion-admin", GUEST) and p.pw_shell not in ("/usr/sbin/nologin", "/bin/false")]
    return {"me": me, "is_guest": me == GUEST, "people": sorted(others),
            "guest_enabled": Path("/etc/orion/guest-enabled").exists(),
            "admin_tool": Path("/opt/orion/everyone/orion-people-admin").exists()}


# =================================================================================================== command line
def wizard() -> int:
    """The first-run question "Need any help seeing, hearing or typing?" (orion-firstrun)."""
    a = Access()
    def ask(q: str) -> bool:
        try:
            return input(f"   {q} [y/N] ").strip().lower() in ("y", "yes")
        except EOFError:
            return False
    print("\n   Each of these can be changed later: Settings > Accessibility, or the keys shown.")
    if ask("Read the screen aloud? (screen reader, Super+Alt+S)"):
        print("   " + a.reader(True))
    if ask("Bigger text?"):
        a.s["text_scale"] = 1.5
    if ask("High contrast: white and yellow on black? (Super+Alt+C)"):
        a.s["contrast"] = True
    if ask("An on-screen keyboard? (Super+Alt+K)"):
        a.s["keyboard"] = True
    if ask("Less movement on screen?"):
        run(["gsettings", "set", "org.gnome.desktop.interface", "enable-animations", "false"])
    if ask("Trouble hearing? (Orion shows everything it says as text; this turns the start-up sound off)"):
        run(["orion-boot-sound", "off"])
    a.save()
    a.apply()
    if a.s["keyboard"]:
        a.osk("on")
    print("   Done." + (f" On: {', '.join(access_words(a.s))}." if access_words(a.s) else ""))
    return 0


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 2
    area, cmd, rest = argv[1], (argv[2] if len(argv) > 2 else "status"), argv[3:]
    as_json = "--json" in rest
    arg = next((x for x in rest if not x.startswith("--")), "")
    # From a key (Sway) the answer shows as a notification; from a terminal or Settings (ORION_QUIET=1) it's printed.
    quiet = sys.stdout.isatty() or os.environ.get("ORION_QUIET") == "1"
    say = lambda m: (print(m), notify("Orion", m) if m and not quiet else None)  # noqa: E731
    if area == "access":
        a = Access()
        flip = lambda cur: {"on": True, "off": False}.get(arg, not cur)  # noqa: E731
        if cmd == "status":
            print(json.dumps(a.s) if as_json else (", ".join(access_words(a.s)) or "Nothing turned on."))
        elif cmd == "session":
            a.session()
        elif cmd == "screen-reader":
            say(a.reader(flip(a.s["screen_reader"])))
        elif cmd == "keyboard":
            msg = a.osk("toggle" if arg in ("", "toggle") else arg)
            if msg:
                say(msg)
        elif cmd == "keyboard-touch":
            a.s["keyboard_touch"] = flip(a.s["keyboard_touch"])
            a.save()
            print("Saved. It applies at the next log-in.")
        elif cmd == "contrast":
            a.s["contrast"] = flip(a.s["contrast"])
            a.save()
            a.apply()
            say("High contrast on." if a.s["contrast"] else "High contrast off.")
        elif cmd == "text-size":
            a.s["text_scale"] = nearest(arg, TEXT_SCALES)
            a.save()
            a.apply()
            say(f"Text size {int(a.s['text_scale'] * 100)}%.")
        elif cmd == "screen-size":
            a.s["screen_scale"] = None if arg in ("off", "", "1") else nearest(arg, SCREEN_SCALES)
            a.save()
            a.apply()
            say("Everything is a bit bigger." if a.s["screen_scale"] else "Normal size.")
        elif cmd == "hud-zoom":
            a.s["hud_zoom"] = arg
            a.s = clean_access(a.s)
            a.save()
            print(f"Orion window zoom {int(a.s['hud_zoom'] * 100)}%.")
        elif cmd == "zoom":
            say(a.zoom(arg if arg in ("in", "out", "reset") else "in"))
        elif cmd == "wizard":
            return wizard()
        else:
            return 2
        return 0
    if area == "typing":
        t = Typing()
        if cmd == "status":
            st = t.status()
            print(json.dumps(st) if as_json else ", ".join(x["name"] for x in st["languages"]))
        elif cmd == "list":
            print("\n".join(f"{k}\t{v[0]}" for k, v in LANGUAGES.items()))
        elif cmd == "add":
            print(t.add(arg))
        elif cmd == "remove":
            print(t.remove(arg))
        elif cmd == "emoji":
            return t.emoji()
        else:
            return 2
        return 0
    if area == "power":
        p = Power()
        if cmd == "status":
            st = p.status()
            print(json.dumps(st) if as_json else
                  (f"Battery {st['percent']}%" if st["present"] else "No battery") + f". Hibernate: {st['hibernate_reason']}")
        elif cmd == "watch":
            return p.watch()
        elif cmd == "once":
            print(json.dumps(p.once()))
        elif cmd in ("auto-saver", "hibernate"):
            key = cmd.replace("-", "_")
            if key == "hibernate" and arg == "on" and not hibernate_possible()[0]:
                print(hibernate_possible()[1])
                return 1
            p.prefs[key] = arg == "on"
            p.save()
            print("Saved.")
        else:
            return 2
        return 0
    if area == "people":
        if cmd == "status":
            st = people_status()
            print(json.dumps(st) if as_json else ", ".join(st["people"]))
        elif cmd == "check-name":
            import pwd
            ok, why = check_login(arg, (p.pw_name for p in pwd.getpwall()))
            print(why or "OK")
            return 0 if ok else 1
        else:
            return 2
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
