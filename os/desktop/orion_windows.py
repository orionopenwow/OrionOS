"""OrionOS window handling for people coming from Windows (H13): the logic behind orion-windows.

Plain data in, data out (tested with captured `swaymsg -t get_tree` output, no screen needed):
  * the list of real app windows (not the Orion HUD, not empty workspaces), with minimised ones (Sway's scratchpad)
    marked, for the Alt+Tab switcher, the HUD taskbar and the title-bar menu;
  * most-recently-used order (the window you were in before comes first in Alt+Tab, like Windows);
  * the "Window buttons" setting as a Sway config snippet (~/.config/orion/sway.d/windows.conf).

Sway has title bars but draws no buttons on them, so "Window buttons" means: a title bar on every app window, and a
right click on it (or Super+W) opens a small glass menu with Close, Maximise, Minimise and Float. Alt+F4 closes.
"""
from __future__ import annotations

import json
import re
from typing import Any, Iterable, Optional

HUD_RE = re.compile(r"(?i)orion")              # the HUD's app id / title (sway-config's own rules use the same)
SETTINGS_ID = "ai.orion.Settings"
SCRATCH = "__i3_scratch"
MENU = [("close", "Close"), ("maximise", "Maximise or restore"), ("minimise", "Minimise"),
        ("float", "Float or snap back into place"), ("next", "Move to the next workspace")]


def _walk(node: dict, workspace: Optional[str] = None) -> Iterable[tuple[dict, Optional[str]]]:
    if node.get("type") == "workspace":
        workspace = node.get("name")
    yield node, workspace
    for child in (node.get("nodes") or []) + (node.get("floating_nodes") or []):
        yield from _walk(child, workspace)


def is_view(node: dict) -> bool:
    """A real window (has a pid and an app id or X11 class), not a split container."""
    return node.get("type") in ("con", "floating_con") and bool(node.get("pid")) and \
        (node.get("app_id") is not None or node.get("window_properties") is not None or node.get("shell") == "xwayland")


def is_hud(node: dict) -> bool:
    app = node.get("app_id") or ""
    if app == SETTINGS_ID:
        return False
    title = node.get("name") or ""
    return bool(HUD_RE.search(app)) or title.lower() == "orion"


def app_name(node: dict) -> str:
    app = node.get("app_id") or ((node.get("window_properties") or {}).get("class")) or ""
    app = app.rsplit(".", 1)[-1] if "." in app else app
    return app[:1].upper() + app[1:] if app else "App"


def windows(tree: Any) -> list[dict]:
    """[{id, app, title, workspace, focused, minimised, urgent, xwayland}] for every app window, in tree order."""
    out: list[dict] = []
    if not isinstance(tree, dict):
        return out
    for node, ws in _walk(tree):
        if not is_view(node) or is_hud(node):
            continue
        title = (node.get("name") or "").strip()
        out.append({
            "id": int(node["id"]),
            "app": app_name(node),
            "app_id": node.get("app_id") or ((node.get("window_properties") or {}).get("class")) or "",
            "title": title or app_name(node),
            "workspace": None if ws == SCRATCH else ws,
            "focused": bool(node.get("focused")),
            "minimised": ws == SCRATCH,
            "urgent": bool(node.get("urgent")),
            "xwayland": node.get("shell") == "xwayland",
        })
    return out


def focused(wins: list[dict]) -> Optional[dict]:
    return next((w for w in wins if w["focused"]), None)


def remember(mru: list[int], con_id: int, keep: int = 50) -> list[int]:
    """Put a newly focused window first."""
    return ([con_id] + [i for i in mru if i != con_id])[:keep]


def mru_order(wins: list[dict], mru: list[int]) -> list[dict]:
    """Windows for Alt+Tab: the one you used before this one first, then older ones, the current window last
    (so Alt+Tab, Enter goes back, like Windows). Windows Orion never saw focused keep their tree order."""
    rank = {cid: i for i, cid in enumerate(mru)}
    cur = focused(wins)
    rest = [w for w in wins if w is not cur]
    rest.sort(key=lambda w: rank.get(w["id"], len(rank) + wins.index(w)))
    return rest + ([cur] if cur else [])


def label(w: dict, width: int = 70) -> str:
    """One line in the switcher: 'Firefox  ·  Page title  (minimised)'."""
    title = w["title"] if w["title"] != w["app"] else ""
    s = w["app"] + (f"  ·  {title}" if title else "")
    if len(s) > width:
        s = s[:width - 1] + "…"
    if w.get("minimised"):
        s += "  (minimised)"
    elif w.get("workspace") and w["workspace"] not in ("1", "2"):
        s += f"  (workspace {w['workspace']})"
    return s


def pick(wins: list[dict], line: str) -> Optional[dict]:
    """The window a switcher line stands for (wofi hands back the text it showed)."""
    line = line.rstrip("\n")
    for w in wins:
        if label(w) == line:
            return w
    return None


def focus_command(w: dict) -> str:
    """Bring a window to the front. A minimised one comes back from the scratchpad into the normal layout."""
    if w.get("minimised"):
        return f"[con_id={w['id']}] scratchpad show, floating disable, focus"
    return f"[con_id={w['id']}] focus"


def action_command(action: str, con_id: int) -> Optional[str]:
    c = f"[con_id={int(con_id)}]"
    return {
        "close": f"{c} kill",
        "maximise": f"{c} fullscreen toggle",
        "minimise": f"{c} move scratchpad",
        "float": f"{c} floating toggle",
        "next": f"{c} move container to workspace next_on_output, workspace next_on_output",
    }.get(action)


def buttons_conf(on: bool) -> str:
    """~/.config/orion/sway.d/windows.conf. On (the default in sway-config): title bars on app windows, always
    shown. Off: thin glowing edges only, as before, hidden when a window is alone on the screen."""
    head = "# Orion Settings > Keyboard & windows: Window buttons (orion-windows buttons on|off)\n"
    if on:
        return head + "default_border normal 2\ndefault_floating_border normal 2\nsmart_borders off\n"
    return head + "default_border pixel 2\ndefault_floating_border pixel 2\nsmart_borders on\n"


def buttons_state(conf_text: Optional[str]) -> bool:
    """Read the setting back (no file = on, the default)."""
    if not conf_text:
        return True
    return not re.search(r"^default_border\s+pixel", conf_text, re.M)


def live_border_command(on: bool) -> str:
    """Apply the setting to windows already open (new ones follow default_border)."""
    return "[all] border normal 2" if on else "[all] border pixel 2"


def parse_mru(text: Optional[str]) -> list[int]:
    try:
        data = json.loads(text or "[]")
        return [int(x) for x in data if isinstance(x, (int, str)) and str(x).isdigit()]
    except (ValueError, TypeError):
        return []
