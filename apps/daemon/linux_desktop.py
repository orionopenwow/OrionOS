"""Linux / Wayland (Sway, or the optional Hyprland session) desktop adapter for OrionOS.

Windows gives Orion screenshots, synthetic input, idle time and the clipboard through Win32.
Wayland deliberately blocks those for ordinary clients, so on OrionOS the daemon talks to the
Sway compositor and a few small wlroots tools instead:

    screenshots      grim                     (wlr-screencopy)
    mouse            the compositor's own IPC (Sway: swaymsg seat - cursor …; Hyprland: hyprctl + wlrctl)
    keyboard         wtype                    (virtual-keyboard protocol)
    windows/outputs  the compositor adapter (compositor.py): Sway's tree, or Hyprland's clients in the same shape
    idle time        swayidle writes $XDG_RUNTIME_DIR/orion-idle-since (see os/lab/sway-config)
    clipboard        wl-paste / wl-copy
    notifications    notify-send (mako)

Everything degrades to "unavailable" when the tools or the session are missing, so the daemon
still starts headless (tests, CI, SSH).
"""
from __future__ import annotations

import io
import json
import logging
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Optional

import compositor

log = logging.getLogger("orion.linux")

IDLE_FILE_NAME = "orion-idle-since"
IDLE_TIMEOUT_S = 5.0  # must match the swayidle timeout in the Sway config
WTYPE_SETTLE_MS = 120


# ------------------------------------------------------------------ session detection
def is_linux() -> bool:
    return sys.platform.startswith("linux")


def is_wayland() -> bool:
    return is_linux() and bool(os.environ.get("WAYLAND_DISPLAY"))


def is_sway() -> bool:
    return is_wayland() and bool(os.environ.get("SWAYSOCK")) and shutil.which("swaymsg") is not None


def is_hyprland() -> bool:
    return is_wayland() and bool(os.environ.get("HYPRLAND_INSTANCE_SIGNATURE")) and shutil.which("hyprctl") is not None


def has_compositor() -> bool:
    """A compositor Orion can talk to (Sway, or the optional Hyprland session)."""
    return is_sway() or is_hyprland()


def _cmd_runner(args: list[str], **kw: Any) -> Optional[str]:
    # swaymsg goes through swaymsg() below (one place to stub in tests); everything else straight to _run
    if args and args[0] == "swaymsg":
        return swaymsg(*args[1:])
    return _run(args, **{k: v for k, v in kw.items() if k == "timeout"})


def backend() -> Optional[compositor.Backend]:
    """The compositor adapter for this session (None when headless)."""
    if not is_wayland():
        return None
    if is_hyprland() and os.environ.get("ORION_COMPOSITOR", "").lower() != "sway":
        return compositor.HyprlandBackend(_cmd_runner)
    if is_sway():
        return compositor.SwayBackend(_cmd_runner)
    return None


def _run(args: list[str], *, timeout: float = 3.0, input_bytes: Optional[bytes] = None,
         binary: bool = False) -> Optional[Any]:
    try:
        out = subprocess.run(args, input=input_bytes, capture_output=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError) as exc:
        log.debug("%s failed: %s", args[0], exc)
        return None
    if out.returncode != 0:
        log.debug("%s exited %s: %s", args[0], out.returncode, out.stderr[:200])
        return None
    return out.stdout if binary else out.stdout.decode("utf-8", "replace")


def swaymsg(*args: str, json_out: bool = False) -> Optional[Any]:
    out = _run(["swaymsg", *args])
    if out is None:
        return None
    if json_out:
        try:
            return json.loads(out)
        except ValueError:
            return None
    return out


# ------------------------------------------------------------------ screen
def screenshot_available() -> bool:
    return is_wayland() and shutil.which("grim") is not None


def grab_screen(output: Optional[str] = None):
    """Screenshot of one output (default: the focused one) as a PIL RGB image, or None."""
    if not screenshot_available():
        return None
    from PIL import Image

    args = ["grim", "-t", "ppm"]
    if output is None:
        output = focused_output()
    if output:
        args += ["-o", output]
    data = _run(args + ["-"], timeout=5.0, binary=True)
    if not data:
        return None
    try:
        return Image.open(io.BytesIO(data)).convert("RGB")
    except Exception:
        log.debug("grim returned unreadable data", exc_info=True)
        return None


def outputs() -> list[dict[str, Any]]:
    b = backend()
    return [o for o in (b.outputs() if b else []) if o.get("active")]


def focused_output() -> Optional[str]:
    for o in outputs():
        if o.get("focused"):
            return o.get("name")
    outs = outputs()
    return outs[0].get("name") if outs else None


def screen_size() -> tuple[int, int]:
    outs = outputs()
    outs.sort(key=lambda o: not o.get("focused"))  # focused output first
    for o in outs:
        rect = o.get("rect") or {}
        if rect.get("width"):
            return int(rect["width"]), int(rect["height"])
    return (1920, 1080)


# ------------------------------------------------------------------ windows
def _walk(node: dict[str, Any]):
    yield node
    for child in node.get("nodes", []) + node.get("floating_nodes", []):
        yield from _walk(child)


def get_tree() -> Optional[dict[str, Any]]:
    """The window tree in Sway's shape (Hyprland's clients are converted by the adapter)."""
    b = backend()
    return b.get_tree() if b else None


def focused_window() -> Optional[dict[str, Any]]:
    tree = get_tree()
    if not tree:
        return None
    for node in _walk(tree):
        if node.get("focused") and node.get("type") in ("con", "floating_con"):
            return node
    return None


SCRATCH = "__i3_scratch"


def _is_hud(node: dict[str, Any]) -> bool:
    app = node.get("app_id") or ""
    if app == "ai.orion.Settings":
        return False
    return "orion" in app.lower() or (node.get("name") or "").lower() == "orion"


def windows_from_tree(tree: Any) -> list[dict[str, Any]]:
    """The real app windows in a `swaymsg -t get_tree` tree for the HUD taskbar (H13): not the HUD itself, not
    split containers. Minimised windows (Sway's scratchpad) are included and marked."""
    out: list[dict[str, Any]] = []
    if not isinstance(tree, dict):
        return out

    def walk(node: dict[str, Any], ws: Optional[str]) -> None:
        if node.get("type") == "workspace":
            ws = node.get("name")
        props = node.get("window_properties") or {}
        if node.get("type") in ("con", "floating_con") and node.get("pid") and \
                (node.get("app_id") or props.get("class")) and not _is_hud(node):
            app = node.get("app_id") or props.get("class") or ""
            short = app.rsplit(".", 1)[-1] if "." in app else app
            out.append({"id": int(node["id"]), "app_id": app, "app": short[:1].upper() + short[1:],
                        "title": (node.get("name") or "").strip() or short, "workspace": None if ws == SCRATCH else ws,
                        "focused": bool(node.get("focused")), "minimised": ws == SCRATCH,
                        "urgent": bool(node.get("urgent"))})
        for child in (node.get("nodes") or []) + (node.get("floating_nodes") or []):
            walk(child, ws)

    walk(tree, None)
    return out


def list_windows() -> list[dict[str, Any]]:
    return windows_from_tree(get_tree())


def focus_window(con_id: int) -> bool:
    """Bring a window to the front (switching workspace); a minimised one comes back from the scratchpad."""
    win = next((w for w in list_windows() if w["id"] == int(con_id)), None)
    if win is None:
        return False
    b = backend() or compositor.SwayBackend(_cmd_runner)   # the window came from a tree, so a session is there
    return b.focus(int(con_id), minimised=bool(win["minimised"]))


def active_window_title() -> Optional[str]:
    node = focused_window()
    if not node:
        return None
    return node.get("name") or node.get("app_id") or None


# ------------------------------------------------------------------ idle
def idle_seconds() -> Optional[float]:
    """Seconds since the last input, from the marker file swayidle maintains."""
    if not is_wayland():
        return None
    runtime = os.environ.get("XDG_RUNTIME_DIR")
    if not runtime:
        return None
    marker = Path(runtime) / IDLE_FILE_NAME
    try:
        since = float(marker.read_text().strip())
    except FileNotFoundError:
        return 0.0  # swayidle removes the marker on resume: the user is active
    except (OSError, ValueError):
        return None
    return max(0.0, time.time() - since + IDLE_TIMEOUT_S)


# ------------------------------------------------------------------ notifications
def notify(title: str, message: str) -> bool:
    if not shutil.which("notify-send"):
        return False
    try:
        subprocess.Popen(["notify-send", "--app-name=Orion", title, message])
        return True
    except OSError:
        return False


# ------------------------------------------------------------------ clipboard
def clipboard_available() -> bool:
    return is_wayland() and shutil.which("wl-paste") is not None


def read_clipboard() -> Optional[str]:
    if not clipboard_available():
        return None
    return _run(["wl-paste", "--no-newline", "--type", "text"], timeout=2.0)


def write_clipboard(text: str) -> bool:
    if not shutil.which("wl-copy"):
        return False
    return _run(["wl-copy"], input_bytes=text.encode("utf-8")) is not None


# ------------------------------------------------------------------ input (pyautogui-compatible)
_BUTTONS = {"left": "button1", "middle": "button2", "right": "button3"}
_KEYS = {
    "enter": "Return", "return": "Return", "esc": "Escape", "escape": "Escape",
    "backspace": "BackSpace", "tab": "Tab", "space": "space", "up": "Up", "down": "Down",
    "left": "Left", "right": "Right", "delete": "Delete", "del": "Delete", "home": "Home",
    "end": "End", "pageup": "Prior", "pagedown": "Next", "insert": "Insert",
    **{f"f{i}": f"F{i}" for i in range(1, 13)},
}
_MODS = {"ctrl": "ctrl", "control": "ctrl", "alt": "alt", "shift": "shift",
         "win": "logo", "windows": "logo", "super": "logo", "cmd": "logo", "command": "logo"}


class SwayAutoGUI:
    """The subset of the pyautogui API that os_automation uses, implemented on the Wayland compositor.

    Mouse actions go through the compositor adapter (Sway's own `seat cursor` commands; on Hyprland, hyprctl for
    moving and wlrctl for clicks); typing goes through wtype. Coordinates are in the compositor's layout space
    (logical pixels), like pyautogui on Windows. (The name stays for the code that already uses it.)
    """

    FAILSAFE = False
    PAUSE = 0.05

    def __init__(self) -> None:
        self._pos = (0, 0)

    @staticmethod
    def available() -> bool:
        return has_compositor()

    @staticmethod
    def _backend() -> compositor.Backend:
        return backend() or compositor.SwayBackend(_cmd_runner)

    def _ok(self, ok: bool, what: str) -> None:
        if not ok:
            raise RuntimeError(f"{what} failed")
        time.sleep(self.PAUSE)

    def size(self) -> tuple[int, int]:
        return screen_size()

    def position(self) -> tuple[int, int]:
        return self._pos

    def moveTo(self, x: int, y: int, duration: float = 0.0) -> None:  # noqa: N802 (pyautogui name)
        self._ok(self._backend().cursor_move(int(x), int(y)), f"moving the pointer to {int(x)},{int(y)}")
        self._pos = (int(x), int(y))

    def click(self, x: Optional[int] = None, y: Optional[int] = None, clicks: int = 1,
              button: str = "left", **_: Any) -> None:
        if x is not None and y is not None:
            self.moveTo(x, y)
        btn = _BUTTONS.get(button, "button1")
        b = self._backend()
        for _ in range(max(1, int(clicks))):
            self._ok(b.click(btn), f"{button} click")

    def scroll(self, clicks: int, x: Optional[int] = None, y: Optional[int] = None) -> None:
        if x is not None and y is not None:
            self.moveTo(x, y)
        btn = "button4" if clicks > 0 else "button5"  # buttons 4/5 are vertical scroll
        b = self._backend()
        for _ in range(abs(int(clicks))):
            self._ok(b.click(btn), "scrolling")

    def dragTo(self, x: int, y: int, duration: float = 0.0, button: str = "left") -> None:  # noqa: N802
        btn = _BUTTONS.get(button, "button1")
        b = self._backend()
        self._ok(b.button(btn, True), "dragging (this compositor can't hold a mouse button)")
        steps = max(1, int(duration / 0.05))
        x0, y0 = self._pos
        for i in range(1, steps + 1):
            self.moveTo(x0 + (x - x0) * i // steps, y0 + (y - y0) * i // steps)
        self._ok(b.button(btn, False), "releasing the mouse button")

    def write(self, text: str, interval: float = 0.0) -> None:
        # "-s 120": each wtype run uploads a fresh keymap and the compositor drops keys sent before it
        # settles (the first character went missing without this).
        args = ["wtype", "-s", str(WTYPE_SETTLE_MS)]
        delay_ms = int(max(0.0, interval) * 1000)
        if delay_ms > 0:  # wtype rejects "-d 0" ("Invalid sleep time")
            args += ["-d", str(delay_ms)]
        if _run(args + ["--", text], timeout=60) is None:
            raise RuntimeError("wtype failed (is it installed and is this a wlroots session?)")

    def press(self, key: str) -> None:
        self.hotkey(key)

    def hotkey(self, *keys: str) -> None:
        mods = [_MODS[k.lower()] for k in keys if k.lower() in _MODS]
        plain = [k for k in keys if k.lower() not in _MODS]
        args = ["wtype", "-s", str(WTYPE_SETTLE_MS)]
        for m in mods:
            args += ["-M", m]
        for k in plain:
            args += ["-k", _KEYS.get(k.lower(), k)]
        for m in reversed(mods):
            args += ["-m", m]
        if _run(args) is None:
            raise RuntimeError(f"wtype {' '.join(keys)} failed")


def status() -> dict[str, Any]:
    """What the Linux adapter can do right now (shown in the HUD and used by tests)."""
    return {
        "linux": is_linux(),
        "wayland": is_wayland(),
        "sway": is_sway(),
        "compositor": getattr(backend(), "name", None),
        "screenshot": screenshot_available(),
        "input": has_compositor() and shutil.which("wtype") is not None,
        "clipboard": clipboard_available(),
        "idle": idle_seconds() is not None,
        "notifications": shutil.which("notify-send") is not None,
    }
