"""One adapter for the Wayland compositor (after-1.0 plan §3: "put compositor calls behind an adapter so Hyprland
can be tried later").

OrionOS 1.0 runs Sway (SwayFX). Hyprland is an optional session (os/desktop/hyprland). Everything Orion asks of the
compositor goes through here, so the rest of the code never calls swaymsg or hyprctl itself:

    backend = compositor.detect()          # SwayBackend, HyprlandBackend, or None (headless / Windows / X11)
    backend.outputs()                      # [{name, active, focused, rect: {x, y, width, height}, scale}]
    backend.get_tree()                     # a Sway-shaped window tree (Hyprland's clients are converted to it)
    backend.focus(con_id, minimised=False)
    backend.window_action("close" | "maximise" | "minimise" | "float" | "next", con_id)
    backend.set_keyboard("gb", "")         # live; the saved setting is the caller's job
    backend.set_borders(True)              # Sway: title bars on windows; Hyprland: nothing to do
    backend.reload()
    backend.cursor_move(x, y); backend.click("button1"); backend.button("button1", True)
    backend.config_text()                  # Sway's running config (None on Hyprland)
    backend.bindings()                     # Hyprland: its key bindings in Sway form, for the shortcut overlay
    backend.subscribe(["window"])          # yields Sway-shaped events: {"change": "focus"|"close", "container": {...}}
    backend.set_screen_shader(path|None)   # Hyprland only: Gravity mode

Plain standard library, no Linux-only imports at module level: the daemon also runs on Windows, where detect()
simply returns None. This exact file is also installed as /opt/orion/desktop/orion_compositor.py for the desktop
commands (orion-windows, orion-keyboard, orion-shortcuts, orion-gravity); os/desktop/orion_compositor.py is a byte
copy kept in sync by a test.
"""
from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
from typing import Any, Callable, Iterator, Optional

Runner = Callable[..., Optional[str]]

SCRATCH = "__i3_scratch"
HYPR_MINIMISED = "special:minimized"
ACTIONS = ("close", "maximise", "minimise", "float", "next")
_BUTTON_NAMES = {"button1": "left", "button2": "middle", "button3": "right"}


def default_run(args: list[str], *, timeout: float = 4.0, **_: Any) -> Optional[str]:
    """Run a command, return its stdout as text, or None if it is missing, fails or times out."""
    try:
        out = subprocess.run(args, capture_output=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    return out.stdout.decode("utf-8", "replace")


def _json(text: Optional[str]) -> Any:
    if not text:
        return None
    try:
        return json.loads(text)
    except ValueError:
        return None


class Backend:
    """What every compositor backend offers. Methods return False / None / [] when something can't be done."""

    name = "none"
    tool = ""

    def __init__(self, run: Optional[Runner] = None, env: Optional[dict[str, str]] = None) -> None:
        self._run = run or default_run
        self.env = env if env is not None else os.environ

    def call(self, *args: str, timeout: float = 4.0) -> Optional[str]:
        return self._run([self.tool, *args], timeout=timeout)

    # every backend implements these
    def outputs(self) -> list[dict[str, Any]]:
        raise NotImplementedError

    def get_tree(self) -> Optional[dict[str, Any]]:
        raise NotImplementedError

    def focus(self, con_id: int, minimised: bool = False) -> bool:
        raise NotImplementedError

    def window_action(self, action: str, con_id: int) -> bool:
        raise NotImplementedError

    def set_keyboard(self, layout: str, variant: str = "") -> bool:
        raise NotImplementedError

    def set_borders(self, on: bool) -> bool:
        raise NotImplementedError

    def reload(self) -> bool:
        raise NotImplementedError

    def exec(self, command: str) -> bool:
        raise NotImplementedError

    def cursor_move(self, x: int, y: int) -> bool:
        raise NotImplementedError

    def button(self, button: str, down: bool) -> bool:
        raise NotImplementedError

    def click(self, button: str = "button1") -> bool:
        return self.button(button, True) and self.button(button, False)

    def config_text(self) -> Optional[str]:
        return None

    def bindings(self) -> list[dict[str, Any]]:
        return []

    def set_screen_shader(self, path: Optional[str]) -> bool:
        return False

    def subscribe(self, kinds: list[str]) -> Iterator[dict[str, Any]]:
        return iter(())

    # shared helpers
    def focused_window(self) -> Optional[dict[str, Any]]:
        tree = self.get_tree()
        if not tree:
            return None
        stack = [tree]
        while stack:
            node = stack.pop()
            if node.get("focused") and node.get("type") in ("con", "floating_con"):
                return node
            stack.extend((node.get("nodes") or []) + (node.get("floating_nodes") or []))
        return None

    def status(self) -> dict[str, Any]:
        return {"name": self.name, "tool": self.tool, "gravity": isinstance(self, HyprlandBackend)}


# ---------------------------------------------------------------------------------------------------------- Sway
class SwayBackend(Backend):
    """Sway / SwayFX through swaymsg (the commands Orion has always used)."""

    name = "sway"
    tool = "swaymsg"

    def msg(self, *args: str) -> Optional[str]:
        return self.call(*args)

    def command(self, cmd: str) -> bool:
        return self.msg(cmd) is not None

    def outputs(self) -> list[dict[str, Any]]:
        data = _json(self.msg("-t", "get_outputs", "-r"))
        out = []
        for o in data or []:
            if not isinstance(o, dict):
                continue
            out.append({"name": o.get("name"), "active": bool(o.get("active")), "focused": bool(o.get("focused")),
                        "rect": o.get("rect") or {}, "scale": o.get("scale") or 1.0})
        return out

    def get_tree(self) -> Optional[dict[str, Any]]:
        tree = _json(self.msg("-t", "get_tree", "-r"))
        return tree if isinstance(tree, dict) else None

    def focus(self, con_id: int, minimised: bool = False) -> bool:
        tail = "scratchpad show, floating disable, focus" if minimised else "focus"
        return self.command(f"[con_id={int(con_id)}] {tail}")

    @staticmethod
    def action_command(action: str, con_id: int) -> Optional[str]:
        c = f"[con_id={int(con_id)}]"
        return {
            "close": f"{c} kill",
            "maximise": f"{c} fullscreen toggle",
            "minimise": f"{c} move scratchpad",
            "float": f"{c} floating toggle",
            "next": f"{c} move container to workspace next_on_output, workspace next_on_output",
        }.get(action)

    def window_action(self, action: str, con_id: int) -> bool:
        cmd = self.action_command(action, con_id)
        return bool(cmd) and self.command(cmd)

    def set_keyboard(self, layout: str, variant: str = "") -> bool:
        ok = self.command(f"input type:keyboard xkb_layout {layout}")
        return self.command(f"input type:keyboard xkb_variant {variant}" if variant else
                            'input type:keyboard xkb_variant ""') and ok

    def set_borders(self, on: bool) -> bool:
        return self.command("[all] border normal 2" if on else "[all] border pixel 2")

    def reload(self) -> bool:
        return self.command("reload")

    def exec(self, command: str) -> bool:
        return self.msg("exec", command) is not None

    def cursor_move(self, x: int, y: int) -> bool:
        return self.msg("seat", "-", "cursor", "set", str(int(x)), str(int(y))) is not None

    def button(self, button: str, down: bool) -> bool:
        return self.msg("seat", "-", "cursor", "press" if down else "release", button) is not None

    def config_text(self) -> Optional[str]:
        data = _json(self.msg("-t", "get_config", "-r"))
        return data.get("config") if isinstance(data, dict) else None

    def subscribe(self, kinds: list[str]) -> Iterator[dict[str, Any]]:
        try:
            p = subprocess.Popen([self.tool, "-m", "-t", "subscribe", json.dumps(list(kinds))],
                                 stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
        except OSError:
            return
        assert p.stdout is not None
        try:
            for line in p.stdout:
                ev = _json(line)
                if isinstance(ev, dict):
                    yield ev
        finally:
            p.terminate()


# ------------------------------------------------------------------------------------------------------ Hyprland
def _addr_int(addr: Any) -> int:
    try:
        return int(str(addr), 16)
    except (TypeError, ValueError):
        return 0


def _addr_hex(con_id: int) -> str:
    return hex(int(con_id))


HYPR_MODS = [(64, "Mod4"), (4, "Control"), (8, "Mod1"), (1, "Shift")]


def hypr_bind_to_sway(b: dict[str, Any]) -> Optional[dict[str, Any]]:
    """One `hyprctl -j binds` entry as {"keys", "command", "flags"} in Sway's words, for the shortcut overlay."""
    key = str(b.get("key") or "")
    if not key or b.get("mouse"):
        return None
    mods = [n for bit, n in HYPR_MODS if int(b.get("modmask") or 0) & bit]
    disp, arg = str(b.get("dispatcher") or ""), str(b.get("arg") or "").strip()
    dirs = {"l": "left", "r": "right", "u": "up", "d": "down"}
    cmd = {
        "exec": f"exec {arg}",
        "killactive": "kill",
        "fullscreen": "fullscreen toggle",
        "togglefloating": "floating toggle",
        "workspace": f"workspace number {arg}",
        "movetoworkspace": f"move container to workspace number {arg}",
        "movefocus": f"focus {dirs.get(arg, arg)}",
        "movewindow": f"move {dirs.get(arg, arg)}",
        "exit": "exit",
    }.get(disp, f"{disp} {arg}".strip())
    return {"keys": "+".join(mods + [key.lower() if len(key) == 1 else key]), "command": cmd, "flags": []}


class HyprlandBackend(Backend):
    """Hyprland through hyprctl and its event socket. Window ids are Hyprland's window addresses as integers."""

    name = "hyprland"
    tool = "hyprctl"

    def j(self, what: str) -> Any:
        return _json(self.call("-j", what))

    def dispatch(self, *args: str) -> bool:
        out = self.call("dispatch", *args)
        return out is not None and not out.strip().lower().startswith(("error", "invalid"))

    def batch(self, *cmds: str) -> bool:
        out = self.call("--batch", " ; ".join(cmds))
        return out is not None and "error" not in out.lower()

    def outputs(self) -> list[dict[str, Any]]:
        out = []
        for m in self.j("monitors") or []:
            if not isinstance(m, dict):
                continue
            scale = float(m.get("scale") or 1.0) or 1.0
            out.append({"name": m.get("name"), "active": not m.get("disabled", False), "focused": bool(m.get("focused")),
                        "rect": {"x": int(m.get("x") or 0), "y": int(m.get("y") or 0),
                                 "width": int(round((m.get("width") or 0) / scale)),
                                 "height": int(round((m.get("height") or 0) / scale))},
                        "scale": scale})
        return out

    @staticmethod
    def tree_from_clients(clients: Any) -> dict[str, Any]:
        """Hyprland's client list as a Sway-shaped tree: root -> workspaces -> windows. Minimised windows (Orion
        keeps them on the special workspace "minimized") land on Sway's scratchpad workspace name."""
        workspaces: dict[str, dict[str, Any]] = {}
        for c in clients if isinstance(clients, list) else []:
            if not isinstance(c, dict) or c.get("mapped") is False:
                continue
            ws = (c.get("workspace") or {}).get("name") or str((c.get("workspace") or {}).get("id", ""))
            ws = SCRATCH if ws in (HYPR_MINIMISED, "special") or ws.startswith("special:min") else ws
            node = {
                "id": _addr_int(c.get("address")),
                "type": "floating_con" if c.get("floating") else "con",
                "app_id": c.get("class") or c.get("initialClass") or None,
                "name": c.get("title") or "",
                "pid": c.get("pid") or None,
                "focused": c.get("focusHistoryID") == 0,
                "urgent": bool(c.get("urgent")),
                "fullscreen_mode": 1 if c.get("fullscreen") else 0,
                "shell": "xwayland" if c.get("xwayland") else "xdg_shell",
            }
            if c.get("xwayland"):
                node["window_properties"] = {"class": c.get("class") or ""}
            w = workspaces.setdefault(ws, {"type": "workspace", "name": ws, "nodes": [], "floating_nodes": []})
            w["floating_nodes" if c.get("floating") else "nodes"].append(node)
        return {"type": "root", "name": "root", "nodes": list(workspaces.values()), "floating_nodes": []}

    def get_tree(self) -> Optional[dict[str, Any]]:
        clients = self.j("clients")
        return self.tree_from_clients(clients) if isinstance(clients, list) else None

    def focus(self, con_id: int, minimised: bool = False) -> bool:
        a = _addr_hex(con_id)
        if minimised:
            ws = self.j("activeworkspace") or {}
            target = str(ws.get("id") or "e+0")
            return self.batch(f"dispatch movetoworkspacesilent {target},address:{a}", f"dispatch focuswindow address:{a}")
        return self.dispatch("focuswindow", f"address:{a}")

    def window_action(self, action: str, con_id: int) -> bool:
        a = f"address:{_addr_hex(con_id)}"
        if action == "close":
            return self.dispatch("closewindow", a)
        if action == "maximise":
            return self.batch(f"dispatch focuswindow {a}", "dispatch fullscreen 1")
        if action == "minimise":
            return self.dispatch("movetoworkspacesilent", f"{HYPR_MINIMISED},{a}")
        if action == "float":
            return self.dispatch("togglefloating", a)
        if action == "next":
            return self.dispatch("movetoworkspace", f"r+1,{a}")
        return False

    def set_keyboard(self, layout: str, variant: str = "") -> bool:
        return self.batch(f"keyword input:kb_layout {layout}", f"keyword input:kb_variant {variant}")

    def set_borders(self, on: bool) -> bool:
        return True   # Hyprland draws no title bars; the window menu (Super+W) and Alt+F4 work the same

    def reload(self) -> bool:
        return self.call("reload") is not None

    def exec(self, command: str) -> bool:
        return self.dispatch("exec", command)

    def cursor_move(self, x: int, y: int) -> bool:
        return self.dispatch("movecursor", str(int(x)), str(int(y)))

    def button(self, button: str, down: bool) -> bool:
        return False   # Hyprland has no IPC for holding a button; click() uses wlrctl's virtual pointer instead

    def click(self, button: str = "button1") -> bool:
        if button in ("button4", "button5"):
            return self._run(["wlrctl", "pointer", "scroll", "-10" if button == "button4" else "10", "0"]) is not None
        return self._run(["wlrctl", "pointer", "click", _BUTTON_NAMES.get(button, "left")]) is not None

    def bindings(self) -> list[dict[str, Any]]:
        out = []
        for b in self.j("binds") or []:
            if isinstance(b, dict):
                conv = hypr_bind_to_sway(b)
                if conv:
                    out.append(conv)
        return out

    def set_screen_shader(self, path: Optional[str]) -> bool:
        # "[[EMPTY]]" is Hyprland's way to clear a string option at runtime
        return self.call("keyword", "decoration:screen_shader", path or "[[EMPTY]]") is not None

    def socket_path(self) -> Optional[str]:
        sig = self.env.get("HYPRLAND_INSTANCE_SIGNATURE")
        if not sig:
            return None
        for base in (os.path.join(self.env.get("XDG_RUNTIME_DIR", ""), "hypr"), "/tmp/hypr"):
            p = os.path.join(base, sig, ".socket2.sock")
            if os.path.exists(p):
                return p
        return None

    @staticmethod
    def event_from_line(line: str) -> Optional[dict[str, Any]]:
        """One socket2 line ("activewindowv2>>55d0c1a2b3c0") as a Sway-shaped window event."""
        name, _, data = line.strip().partition(">>")
        if name == "activewindowv2" and data and data != ",":
            return {"change": "focus", "container": {"id": _addr_int(data.split(",")[0])}}
        if name == "closewindow" and data:
            return {"change": "close", "container": {"id": _addr_int(data)}}
        if name == "openwindow" and data:
            parts = data.split(",", 3)
            return {"change": "new", "container": {"id": _addr_int(parts[0]),
                                                   "app_id": parts[2] if len(parts) > 2 else None,
                                                   "name": parts[3] if len(parts) > 3 else ""}}
        return None

    def subscribe(self, kinds: list[str]) -> Iterator[dict[str, Any]]:
        if "window" not in kinds:
            return   # Hyprland has no binding events; callers just don't get any
        path = self.socket_path()
        family = getattr(socket, "AF_UNIX", None)
        if not path or family is None:
            return
        s = socket.socket(family, socket.SOCK_STREAM)
        try:
            s.connect(path)
            with s.makefile("r", encoding="utf-8", errors="replace") as f:
                for line in f:
                    ev = self.event_from_line(line)
                    if ev:
                        yield ev
        except OSError:
            return
        finally:
            s.close()


# ------------------------------------------------------------------------------------------------------ detection
def detect(env: Optional[dict[str, str]] = None, run: Optional[Runner] = None,
           which: Callable[[str], Optional[str]] = shutil.which) -> Optional[Backend]:
    """The compositor this session runs, or None. Hyprland is checked first (its session also sets
    WAYLAND_DISPLAY); ORION_COMPOSITOR=sway|hyprland forces one when both sockets are around."""
    env = env if env is not None else dict(os.environ)
    if not sys.platform.startswith("linux") and not env.get("ORION_COMPOSITOR_ANY_OS"):
        return None
    forced = (env.get("ORION_COMPOSITOR") or "").lower()
    hypr = bool(env.get("HYPRLAND_INSTANCE_SIGNATURE")) and which("hyprctl") is not None
    sway = bool(env.get("SWAYSOCK")) and which("swaymsg") is not None
    if hypr and forced != "sway":
        return HyprlandBackend(run, env)
    if sway:
        return SwayBackend(run, env)
    return None


def name(env: Optional[dict[str, str]] = None) -> str:
    b = detect(env)
    return b.name if b else "none"
