"""Accessibility identity for the desktop Skill Recorder (after-1.0 plan §1, Phase 2).

Each click is saved as WHAT was clicked (app, window title, element role + name, automation id), not where.
The screen position is kept only as a backup. Identity comes from:
  Linux    AT-SPI, through gi.repository.Atspi (apt: gir1.2-atspi-2.0 python3-gi) or pyatspi
  Windows  UI Automation, through the `uiautomation` package (pip, optional)
  neither  NullBackend: window titles only (Sway tree / foreground window); clicks keep only their position

Every import here is optional and guarded: the daemon starts on Windows and Linux without any of them.

Privacy (never recorded, recording pauses and says so):
  - password fields (AT-SPI role "password text", UI Automation IsPassword) and fields named like card numbers or
    one-time codes (skills_recorder.sensitive_field_reason, the browser recorder's rule)
  - windows of password managers, banks and payment apps, private browser windows, login / unlock prompts, the
    lock screen and windows on the user's own exclude list (capture_privacy.why_skip_window, which uses the
    extended macro_recorder.SENSITIVE_TITLE rule)
"""
from __future__ import annotations

import logging
import re
import sys
from difflib import SequenceMatcher
from typing import Any, Optional

log = logging.getLogger("orion.desktop_access")

# ---------------------------------------------------------------------------------- roles
ROLE_MAP = {
    # AT-SPI role names
    "push button": "button", "toggle button": "button", "button": "button", "menu item": "menuitem",
    "check menu item": "menuitem", "radio menu item": "menuitem", "menu": "menu", "link": "link",
    "check box": "checkbox", "radio button": "radio", "page tab": "tab", "list item": "listitem",
    "table cell": "cell", "tree item": "treeitem", "combo box": "combobox", "text": "textbox", "entry": "textbox",
    "password text": "password", "spin button": "spinbutton", "slider": "slider", "icon": "icon",
    "label": "label", "terminal": "terminal", "document text": "document", "paragraph": "document",
    "tool bar": "toolbar", "frame": "window", "dialog": "dialog", "alert": "dialog", "file chooser": "dialog",
    # UI Automation control type names (without the "Control" suffix)
    "edit": "textbox", "hyperlink": "link", "menuitem": "menuitem", "checkbox": "checkbox",
    "radiobutton": "radio", "tabitem": "tab", "listitem": "listitem", "treeitem": "treeitem",
    "combobox": "combobox", "dataitem": "cell", "splitbutton": "button", "document": "document",
    "window": "window", "pane": "pane", "image": "icon", "spinner": "spinbutton",
}
TEXT_ROLES = {"textbox", "combobox", "spinbutton", "document", "terminal", "password"}


def norm_role(raw: str) -> str:
    r = re.sub(r"control$", "", str(raw or "").strip().lower()).strip()
    return ROLE_MAP.get(r, ROLE_MAP.get(r.replace(" ", ""), r.replace(" ", "")))


def _clean(s: Any, n: int = 120) -> str:
    return re.sub(r"\s+", " ", str(s or "")).strip()[:n]


def make_element(*, app: str = "", window: str = "", role: str = "", name: str = "", automation_id: str = "",
                 rect: Optional[tuple[int, int, int, int]] = None, password: bool = False,
                 ref: Any = None) -> dict[str, Any]:
    """The plain dict every backend hands out. rect = (x, y, w, h) in screen pixels. `_ref` is the native object
    (never saved: skills keep only the plain fields)."""
    el: dict[str, Any] = {"app": _clean(app, 80), "window": _clean(window, 200), "role": norm_role(role),
                          "name": _clean(name), "automation_id": _clean(automation_id, 120),
                          "password": bool(password)}
    if rect and rect[2] > 0 and rect[3] > 0:
        el["rect"] = [int(rect[0]), int(rect[1]), int(rect[2]), int(rect[3])]
    if ref is not None:
        el["_ref"] = ref
    return el


def center(el: dict[str, Any]) -> Optional[tuple[int, int]]:
    r = el.get("rect")
    if r:
        return int(r[0] + r[2] / 2), int(r[1] + r[3] / 2)
    if el.get("x") is not None and el.get("y") is not None:
        return int(el["x"]), int(el["y"])
    return None


def target_from_element(el: dict[str, Any], x: Optional[int] = None, y: Optional[int] = None) -> dict[str, Any]:
    """A saved step target: identity first, the position last (backup only)."""
    t: dict[str, Any] = {k: el[k] for k in ("app", "window", "role", "name", "automation_id") if el.get(k)}
    sels: list[dict[str, Any]] = []
    if el.get("automation_id"):
        sels.append({"kind": "automation_id", "value": el["automation_id"]})
    if el.get("role") and el.get("name"):
        sels.append({"kind": "role", "role": el["role"], "name": el["name"]})
    if el.get("name"):
        sels.append({"kind": "text", "value": el["name"]})
    pos = (x, y) if x is not None and y is not None else center(el)
    if pos:
        sels.append({"kind": "coords", "x": int(pos[0]), "y": int(pos[1])})
    t["selectors"] = sels
    return t


# ---------------------------------------------------------------------------------- privacy
def private_reason(window: dict[str, Any] | None, element: dict[str, Any] | None = None,
                   words: Optional[list[str]] = None) -> str:
    """'' = fine to record; otherwise a plain reason ("a password manager", "a password field")."""
    try:
        import capture_privacy
        own = capture_privacy.own_words() if words is None else words
        why = capture_privacy.why_skip_window((window or {}).get("title", "") or (element or {}).get("window", ""),
                                              (window or {}).get("app", "") or (element or {}).get("app", ""), own)
    except Exception:  # pragma: no cover - fall back to the plain title rule
        from macro_recorder import SENSITIVE_TITLE
        why = "a login or password prompt" if SENSITIVE_TITLE.search((window or {}).get("title", "")) else ""
    if why:
        return why
    el = element or {}
    if el.get("password") or el.get("role") == "password":
        return "a password field"
    if el.get("role") in TEXT_ROLES or el.get("role") in ("", None):
        try:
            from skills_recorder import sensitive_field_reason
            kind = sensitive_field_reason({"name": el.get("name", ""), "label": el.get("name", ""),
                                           "field_id": el.get("automation_id", "")})
        except Exception:
            kind = None
        if kind:
            return {"password": "a password field", "card": "a card number field",
                    "code": "a one-time code field"}.get(kind, "a private field")
    return ""


# ---------------------------------------------------------------------------------- backends
class NullBackend:
    """No accessibility: only the active window's title (Sway tree on OrionOS)."""
    name = "none"
    identity = False

    def available(self) -> bool:
        return True

    def active_window(self) -> dict[str, Any]:
        try:
            import linux_desktop
            node = linux_desktop.focused_window() if linux_desktop.is_sway() else None
            if node:
                props = node.get("window_properties") or {}
                return {"app": str(node.get("app_id") or props.get("class") or ""), "title": str(node.get("name") or "")}
        except Exception:
            pass
        try:
            from capture_engine import active_window_title
            return {"app": "", "title": active_window_title() or ""}
        except Exception:
            return {"app": "", "title": ""}

    def element_at(self, x: int, y: int) -> Optional[dict[str, Any]]:
        return None

    def focused_element(self) -> Optional[dict[str, Any]]:
        return None

    def elements(self, app: str = "", window: str = "") -> list[dict[str, Any]]:
        return []

    def windows(self) -> list[dict[str, Any]]:
        try:
            import linux_desktop
            return [{"app": w["app_id"], "title": w["title"], "id": w["id"]} for w in linux_desktop.list_windows()]
        except Exception:
            return []

    def activate(self, win: dict[str, Any]) -> bool:
        try:
            import linux_desktop
            if win.get("id") is not None and linux_desktop.is_sway():
                return linux_desktop.focus_window(int(win["id"]))
        except Exception:
            pass
        return False

    def do(self, el: dict[str, Any], action: str = "click") -> bool:
        return False

    def set_text(self, el: dict[str, Any], text: str) -> bool:
        return False


class AtspiBackend(NullBackend):
    """Linux accessibility through AT-SPI (GTK, Qt, Firefox, LibreOffice, Electron with a11y on...)."""
    name = "atspi"
    identity = True
    MAX_NODES = 4000

    def __init__(self) -> None:
        self.A: Any = None
        self.py: Any = None
        try:
            import gi  # type: ignore
            gi.require_version("Atspi", "2.0")
            from gi.repository import Atspi  # type: ignore
            self.A = Atspi
        except Exception:
            try:
                import pyatspi  # type: ignore
                self.py = pyatspi
                self.A = pyatspi.Atspi if hasattr(pyatspi, "Atspi") else None
            except Exception:
                self.A = None

    def available(self) -> bool:
        if self.A is None:
            return False
        try:
            return self.A.get_desktop(0) is not None
        except Exception:
            return False

    def _el(self, acc: Any, app: str = "", window: str = "") -> Optional[dict[str, Any]]:
        A = self.A
        try:
            rect = None
            comp = acc.get_component_iface()
            if comp is not None:
                e = comp.get_extents(A.CoordType.SCREEN)
                rect = (e.x, e.y, e.width, e.height)
            role = acc.get_role_name() or ""
            pw = acc.get_role() == A.Role.PASSWORD_TEXT
            if not app:
                a = acc.get_application()
                app = a.get_name() if a is not None else ""
            return make_element(app=app, window=window or self._window_of(acc), role=role, name=acc.get_name() or "",
                                automation_id=(acc.get_accessible_id() if hasattr(acc, "get_accessible_id") else "") or "",
                                rect=rect, password=pw, ref=acc)
        except Exception:
            return None

    def _window_of(self, acc: Any) -> str:
        A = self.A
        node, hops = acc, 0
        while node is not None and hops < 60:
            try:
                if node.get_role() in (A.Role.FRAME, A.Role.DIALOG, A.Role.WINDOW, A.Role.FILE_CHOOSER, A.Role.ALERT):
                    return node.get_name() or ""
                node = node.get_parent()
            except Exception:
                return ""
            hops += 1
        return ""

    def _frames(self) -> list[tuple[Any, Any]]:
        A = self.A
        out = []
        desk = A.get_desktop(0)
        for i in range(desk.get_child_count()):
            app = desk.get_child_at_index(i)
            if app is None:
                continue
            for j in range(app.get_child_count()):
                w = app.get_child_at_index(j)
                if w is not None:
                    out.append((app, w))
        return out

    def _active(self) -> tuple[Any, Any]:
        A = self.A
        for app, w in self._frames():
            try:
                if w.get_state_set().contains(A.StateType.ACTIVE):
                    return app, w
            except Exception:
                continue
        return None, None

    def active_window(self) -> dict[str, Any]:
        try:
            app, w = self._active()
            if w is not None:
                return {"app": app.get_name() or "", "title": w.get_name() or "", "_ref": w}
        except Exception:
            log.debug("AT-SPI active window failed", exc_info=True)
        return super().active_window()

    def element_at(self, x: int, y: int) -> Optional[dict[str, Any]]:
        A = self.A
        try:
            app, w = self._active()
            if w is None:
                return None
            node, best = w, None
            for _ in range(40):  # walk down to the deepest element under the point
                comp = node.get_component_iface()
                if comp is None:
                    break
                child = comp.get_accessible_at_point(int(x), int(y), A.CoordType.SCREEN)
                if child is None or child == node:
                    break
                best = node = child
            return self._el(best or w, app.get_name() or "", w.get_name() or "")
        except Exception:
            log.debug("AT-SPI element_at failed", exc_info=True)
            return None

    def focused_element(self) -> Optional[dict[str, Any]]:
        A = self.A
        try:
            app, w = self._active()
            if w is None:
                return None
            for acc in self._walk(w):
                if acc.get_state_set().contains(A.StateType.FOCUSED):
                    return self._el(acc, app.get_name() or "", w.get_name() or "")
        except Exception:
            log.debug("AT-SPI focused_element failed", exc_info=True)
        return None

    def _walk(self, root: Any):
        stack, seen = [root], 0
        while stack and seen < self.MAX_NODES:
            node = stack.pop()
            seen += 1
            yield node
            try:
                n = node.get_child_count()
                for i in range(min(n, 400) - 1, -1, -1):
                    c = node.get_child_at_index(i)
                    if c is not None:
                        stack.append(c)
            except Exception:
                continue

    def windows(self) -> list[dict[str, Any]]:
        try:
            return [{"app": a.get_name() or "", "title": w.get_name() or "", "_ref": w} for a, w in self._frames()]
        except Exception:
            return super().windows()

    def activate(self, win: dict[str, Any]) -> bool:
        if super().activate(win):  # Sway focus by id when we have one
            return True
        try:
            import linux_desktop
            for w in linux_desktop.list_windows():
                if w["title"] == win.get("title"):
                    return linux_desktop.focus_window(w["id"])
        except Exception:
            pass
        return False

    def elements(self, app: str = "", window: str = "") -> list[dict[str, Any]]:
        out = []
        try:
            for a, w in self._frames():
                an, wn = a.get_name() or "", w.get_name() or ""
                if app and an.lower() != app.lower():
                    continue
                if window and wn != window and not similar(wn, window, 0.6):
                    continue
                for acc in self._walk(w):
                    if acc is w:
                        continue
                    el = self._el(acc, an, wn)
                    if el and (el["name"] or el["automation_id"]):
                        out.append(el)
        except Exception:
            log.debug("AT-SPI elements failed", exc_info=True)
        return out

    def do(self, el: dict[str, Any], action: str = "click") -> bool:
        acc = el.get("_ref")
        if acc is None:
            return False
        try:
            if action == "focus":
                comp = acc.get_component_iface()
                return bool(comp and comp.grab_focus())
            act = acc.get_action_iface()
            if act is None:
                return False
            names = [act.get_action_name(i) for i in range(act.get_n_actions())]
            for want in ("click", "press", "activate", "jump", "toggle"):
                if want in names:
                    return bool(act.do_action(names.index(want)))
            return bool(names) and bool(act.do_action(0))
        except Exception:
            return False

    def set_text(self, el: dict[str, Any], text: str) -> bool:
        acc = el.get("_ref")
        if acc is None or el.get("password"):
            return False
        try:
            et = acc.get_editable_text_iface()
            return bool(et and et.set_text_contents(text))
        except Exception:
            return False


class UiaBackend(NullBackend):
    """Windows accessibility through UI Automation (`pip install uiautomation`)."""
    name = "uia"
    identity = True

    def __init__(self) -> None:
        try:
            import uiautomation as auto  # type: ignore
            self.auto = auto
        except Exception:
            self.auto = None

    def available(self) -> bool:
        return self.auto is not None

    def _app_name(self, ctrl: Any) -> str:
        try:
            import psutil  # type: ignore
            return psutil.Process(ctrl.ProcessId).name().removesuffix(".exe")
        except Exception:
            return ""

    def _el(self, ctrl: Any, top: Any = None) -> Optional[dict[str, Any]]:
        if ctrl is None:
            return None
        try:
            top = top or ctrl.GetTopLevelControl()
            r = ctrl.BoundingRectangle
            rect = (r.left, r.top, r.right - r.left, r.bottom - r.top)
            pw = False
            try:
                pw = bool(ctrl.GetPropertyValue(self.auto.PropertyId.IsPasswordProperty))
            except Exception:
                pass
            return make_element(app=self._app_name(top or ctrl), window=(top.Name if top else "") or "",
                                role=ctrl.ControlTypeName or "", name=ctrl.Name or "",
                                automation_id=ctrl.AutomationId or "", rect=rect, password=pw, ref=ctrl)
        except Exception:
            return None

    def active_window(self) -> dict[str, Any]:
        try:
            top = self.auto.GetForegroundControl().GetTopLevelControl()
            return {"app": self._app_name(top), "title": top.Name or "", "_ref": top}
        except Exception:
            return {"app": "", "title": ""}

    def element_at(self, x: int, y: int) -> Optional[dict[str, Any]]:
        try:
            return self._el(self.auto.ControlFromPoint(int(x), int(y)))
        except Exception:
            return None

    def focused_element(self) -> Optional[dict[str, Any]]:
        try:
            return self._el(self.auto.GetFocusedControl())
        except Exception:
            return None

    def windows(self) -> list[dict[str, Any]]:
        try:
            return [{"app": self._app_name(w), "title": w.Name or "", "_ref": w}
                    for w in self.auto.GetRootControl().GetChildren() if w.Name]
        except Exception:
            return []

    def activate(self, win: dict[str, Any]) -> bool:
        w = win.get("_ref")
        try:
            return bool(w and w.SetActive())
        except Exception:
            return False

    def elements(self, app: str = "", window: str = "") -> list[dict[str, Any]]:
        out = []
        try:
            for w in self.auto.GetRootControl().GetChildren():
                if window and w.Name != window and not similar(w.Name or "", window, 0.6):
                    continue
                if app and self._app_name(w).lower() != app.lower():
                    continue
                for ctrl, _depth in self.auto.WalkControl(w, maxDepth=25):
                    el = self._el(ctrl, w)
                    if el and (el["name"] or el["automation_id"]):
                        out.append(el)
                    if len(out) > 4000:
                        return out
        except Exception:
            log.debug("UIA elements failed", exc_info=True)
        return out

    def do(self, el: dict[str, Any], action: str = "click") -> bool:
        ctrl = el.get("_ref")
        if ctrl is None:
            return False
        try:
            if action == "focus":
                return bool(ctrl.SetFocus())
            for get in ("GetInvokePattern", "GetTogglePattern", "GetSelectionItemPattern"):
                try:
                    pat = getattr(ctrl, get)()
                except Exception:
                    pat = None
                if pat is not None:
                    for verb in ("Invoke", "Toggle", "Select"):
                        if hasattr(pat, verb):
                            getattr(pat, verb)()
                            return True
            ctrl.Click(simulateMove=False)
            return True
        except Exception:
            return False

    def set_text(self, el: dict[str, Any], text: str) -> bool:
        ctrl = el.get("_ref")
        if ctrl is None or el.get("password"):
            return False
        try:
            ctrl.GetValuePattern().SetValue(text)
            return True
        except Exception:
            return False


def pick_backend() -> NullBackend:
    """The best accessibility source on this machine (NullBackend when there is none)."""
    try:
        if sys.platform == "win32":
            b: NullBackend = UiaBackend()
        elif sys.platform.startswith("linux"):
            b = AtspiBackend()
        else:
            b = NullBackend()
        if b.available():
            return b
    except Exception:
        log.debug("accessibility backend failed", exc_info=True)
    return NullBackend()


def similar(a: str, b: str, at_least: float = 0.82) -> bool:
    return ratio(a, b) >= at_least


def ratio(a: str, b: str) -> float:
    a, b = _clean(a).lower(), _clean(b).lower()
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    return SequenceMatcher(None, a, b).ratio()


def status() -> dict[str, Any]:
    b = pick_backend()
    return {"backend": b.name, "identity": b.identity}
