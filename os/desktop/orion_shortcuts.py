"""OrionOS shortcut overlay (cheap feature 5): the real keyboard shortcuts, read from the Sway config in use.

Hold Super (or press Super+/) and a glass panel lists them, grouped and in plain words. Nothing is hard-coded: the
config is read the way Sway reads it (variables, include lines with globs, line continuations; mode and bar blocks
skipped), so a shortcut you add yourself shows up too, described by its command when Orion doesn't know it.
Pure functions; tested with sample config files.
"""
from __future__ import annotations

import glob
import os
import re
import shlex
from pathlib import Path
from typing import Callable, Optional

GROUPS = ["Windows", "Apps and Orion", "Workspaces", "Screen and sound", "Screenshots and clipboard",
          "Accessibility", "More"]

KEY_WORDS = {
    "mod4": "Super", "super": "Super", "mod1": "Alt", "alt": "Alt", "ctrl": "Ctrl", "control": "Ctrl",
    "shift": "Shift", "mod5": "AltGr", "return": "Enter", "comma": ",", "period": ".", "slash": "/",
    "equal": "=", "minus": "-", "space": "Space", "tab": "Tab", "escape": "Esc", "print": "Print Screen",
    "left": "Left", "right": "Right", "up": "Up", "down": "Down", "delete": "Delete", "backspace": "Backspace",
    "xf86monbrightnessup": "Brightness up key", "xf86monbrightnessdown": "Brightness down key",
    "xf86audioraisevolume": "Volume up key", "xf86audiolowervolume": "Volume down key",
    "xf86audiomute": "Mute key", "xf86audiomicmute": "Mic mute key", "xf86display": "Display key",
    "button1": "Left click", "button2": "Middle click", "button3": "Right click",
    "super_l": "Super", "super_r": "Super",
}

DIRS = {"left": "on the left", "right": "on the right", "up": "above", "down": "below"}
# (regex on the command, words, group). First match wins; {1} etc. are the regex groups.
KNOWN: list[tuple[str, str, str]] = [
    (r"^workspace number 1$", "Orion (back to the Orion screen)", "Apps and Orion"),
    (r"^workspace number (\d+)$", "Go to workspace {1}", "Workspaces"),
    (r"^move container to workspace number (\d+)$", "Move the window to workspace {1}", "Workspaces"),
    (r"^kill$", "Close the window", "Windows"),
    (r"^fullscreen( toggle)?$", "Full screen on or off", "Windows"),
    (r"^floating toggle$", "Float the window, or snap it back", "Windows"),
    (r"^move scratchpad$", "Minimise the window", "Windows"),
    (r"^scratchpad show$", "Bring back a minimised window", "Windows"),
    (r"^focus (left|right|up|down)$", "Go to the window {d1}", "Windows"),
    (r"^move (left|right|up|down)$", "Move the window {1}", "Windows"),
    (r"^reload$", "Reload the desktop's settings", "More"),
    (r"exec orion-windows switch", "Switch between windows", "Windows"),
    (r"exec orion-windows menu", "Window menu: close, maximise, minimise", "Windows"),
    (r"exec orion-windows do (\w+)", "{1} the window", "Windows"),
    (r"exec orion-shortcuts", "Show these shortcuts", "More"),
    (r"exec (wofi --show drun|\$menu)", "Open an app", "Apps and Orion"),
    (r"exec (foot|\$term)\b", "Terminal", "Apps and Orion"),
    (r"exec orion-files", "Files", "Apps and Orion"),
    (r"exec orion-settings", "Settings", "Apps and Orion"),
    (r"exec orion-lock", "Lock the screen", "Apps and Orion"),
    (r"orion-ctl mic toggle", "Microphone on or off", "Screen and sound"),
    (r"orion-ctl camera toggle", "Camera presence and gestures on or off", "Screen and sound"),
    (r"swaynag .*exit", "Log out", "Apps and Orion"),
    (r"exec orion-screenshot full", "Screenshot of the whole screen", "Screenshots and clipboard"),
    (r"exec orion-screenshot area", "Screenshot of an area", "Screenshots and clipboard"),
    (r"exec orion-screenshot markup", "Screenshot of an area, then draw on it", "Screenshots and clipboard"),
    (r"exec orion-clipboard pick", "Clipboard history", "Screenshots and clipboard"),
    (r"exec orion-share-scrub", "Clean the clipboard before sharing", "Screenshots and clipboard"),
    (r"exec orion-nightlight toggle", "Night light on or off", "Screen and sound"),
    (r"exec orion-displays arrange", "Arrange screens", "Screen and sound"),
    (r"brightnessctl set \+", "Brighter screen", "Screen and sound"),
    (r"brightnessctl set .*-", "Dimmer screen", "Screen and sound"),
    (r"wpctl set-volume .*%\+", "Louder", "Screen and sound"),
    (r"wpctl set-volume .*%-", "Quieter", "Screen and sound"),
    (r"wpctl set-mute", "Sound on or off", "Screen and sound"),
    (r"orion-access screen-reader", "Screen reader on or off", "Accessibility"),
    (r"orion-access keyboard", "On-screen keyboard", "Accessibility"),
    (r"orion-access contrast", "High contrast on or off", "Accessibility"),
    (r"orion-access zoom in", "Zoom in", "Accessibility"),
    (r"orion-access zoom out", "Zoom out", "Accessibility"),
    (r"orion-access zoom reset", "Zoom back to normal", "Accessibility"),
    (r"orion-typing emoji", "Emoji", "Accessibility"),
]


def strip_comment(line: str) -> str:
    return "" if line.lstrip().startswith("#") else line


def logical_lines(text: str) -> list[str]:
    """Join backslash continuations; keep comment lines as they are."""
    out, cur = [], ""
    for raw in text.splitlines():
        if raw.rstrip().endswith("\\") and not raw.lstrip().startswith("#"):
            cur += raw.rstrip()[:-1] + " "
            continue
        out.append(cur + raw)
        cur = ""
    if cur:
        out.append(cur)
    return out


def expand_vars(s: str, variables: dict[str, str]) -> str:
    for k in sorted(variables, key=len, reverse=True):
        s = s.replace(k, variables[k])
    return s


def expand_path(p: str, env: dict[str, str], home: str, base: str) -> list[str]:
    p = p.strip().strip('"').strip("'")
    p = re.sub(r"\$\{?([A-Z_][A-Z0-9_]*)\}?", lambda m: env.get(m[1], ""), p)
    if p.startswith("~"):
        p = home + p[1:]
    if not os.path.isabs(p):
        p = os.path.join(base, p)
    return sorted(glob.glob(p))


def parse(text: str, *, env: Optional[dict[str, str]] = None, home: str = "~", base: str = ".",
          read: Callable[[str], Optional[str]] = None, _vars: Optional[dict[str, str]] = None,
          _depth: int = 0, _seen: Optional[set] = None) -> list[dict]:
    """All keyboard/mouse bindings in a Sway config (following includes) as
    [{"keys": "Mod4+d", "command": "exec wofi --show drun", "flags": [...]}] in config order."""
    env = env if env is not None else dict(os.environ)
    read = read or _read
    variables = _vars if _vars is not None else {}
    seen = _seen if _seen is not None else set()
    out: list[dict] = []
    depth_block = 0
    for line in logical_lines(text):
        s = strip_comment(line).strip()
        if not s:
            continue
        if depth_block:
            depth_block += s.count("{") - s.count("}")
            continue
        if re.match(r"^(mode|bar)\b.*\{$", s) or (s.endswith("{") and not s.startswith(("bindsym", "bindcode"))):
            depth_block = 1
            continue
        m = re.match(r"^set\s+(\$\S+)\s+(.*)$", s)
        if m:
            variables[m[1]] = expand_vars(m[2].strip(), variables)
            continue
        m = re.match(r"^include\s+(.+)$", s)
        if m:
            if _depth >= 5:
                continue
            for path in expand_path(expand_vars(m[1], variables), env, home, base):
                if path in seen:
                    continue
                seen.add(path)
                t = read(path)
                if t is not None:
                    out += parse(t, env=env, home=home, base=os.path.dirname(path), read=read, _vars=variables,
                                 _depth=_depth + 1, _seen=seen)
            continue
        m = re.match(r"^bindsym\s+(.*)$", s)
        if not m:
            continue
        rest = expand_vars(m[1], variables)
        try:
            parts = shlex.split(rest, posix=True)
        except ValueError:
            parts = rest.split()
        flags = []
        while parts and parts[0].startswith("--"):
            flags.append(parts.pop(0))
        if len(parts) < 2:
            continue
        keys, command = parts[0], " ".join(parts[1:])
        out.append({"keys": keys, "command": command, "flags": flags})
    return out


def _read(path: str) -> Optional[str]:
    try:
        return Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def pretty_keys(keys: str) -> str:
    out = []
    for k in keys.split("+"):
        w = KEY_WORDS.get(k.lower())
        if w is None:
            w = k.upper() if len(k) == 1 else k
        out.append(w)
    return "+".join(out)


def describe(command: str) -> tuple[str, str]:
    """(plain words, group) for a binding's command."""
    c = command.strip()
    for rx, words, group in KNOWN:
        m = re.search(rx, c)
        if m:
            text = words
            for i, g in enumerate(m.groups(), 1):
                text = text.replace("{d%d}" % i, DIRS.get(g or "", g or "")).replace("{%d}" % i, g or "")
            return text[:1].upper() + text[1:], group
    m = re.match(r"^exec(?:_always)?\s+(?:--no-startup-id\s+)?(\S+)", c)
    if m:
        return f"Run {os.path.basename(m[1])}", "More"
    return c[:60], "More"


def is_hold(b: dict) -> bool:
    return b["keys"].lower() in ("super_l", "super_r", "mod4")


def shortcuts(bindings: list[dict]) -> list[dict]:
    """Grouped and merged for the overlay: [{"group", "items": [{"keys": ["Super+D", "Ctrl+Alt+Space"], "does"}]}].
    Twin keys for the same action are shown together; Orion's own hold-Super binding is listed once as 'Hold Super'."""
    items: dict[tuple[str, str], dict] = {}
    for b in bindings:
        if is_hold(b):
            if "--release" in b["flags"]:
                continue
            key = ("More", "Show these shortcuts")
            items.setdefault(key, {"group": "More", "does": "Show these shortcuts", "keys": []})
            if "Hold Super" not in items[key]["keys"]:
                items[key]["keys"].append("Hold Super")
            continue
        does, group = describe(b["command"])
        k = pretty_keys(b["keys"])
        if k.startswith(("Left click", "Middle click", "Right click")):
            k += " on a title bar"
        key = (group, does)
        it = items.setdefault(key, {"group": group, "does": does, "keys": []})
        if k not in it["keys"]:
            it["keys"].append(k)
    out = []
    for g in GROUPS:
        rows = [{"keys": v["keys"], "does": v["does"]} for (grp, _d), v in items.items() if grp == g]
        if rows:
            out.append({"group": g, "items": rows})
    return out


def active_config(get_config_json: Optional[dict], fallback_paths: list[str]) -> tuple[Optional[str], str]:
    """The text of the config Sway is running (swaymsg -t get_config), else the first file that exists.
    Returns (text, directory includes are relative to)."""
    if isinstance(get_config_json, dict) and get_config_json.get("config"):
        for p in fallback_paths:
            if os.path.isfile(p):
                return get_config_json["config"], os.path.dirname(p)
        return get_config_json["config"], os.path.expanduser("~/.config/sway")
    for p in fallback_paths:
        t = _read(p)
        if t is not None:
            return t, os.path.dirname(p)
    return None, "."
