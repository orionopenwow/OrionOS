"""Desktop skills: the step model, plain words, risk rules, variables, and Python import/export.

A desktop skill is the same signed JSON as a browser skill (skills_recorder.SkillStore) with "surface": "desktop"
on the skill and on every step. Step actions:

  focus     {app, window}                 bring that window forward (opens the app when it isn't running)
  click     {target, button?, double?}    target = {app, window, role, name, automation_id, selectors}
  type      {target?, value}              typed text, saved per field (never raw key presses)
  shortcut  {keys: ["ctrl", "s"]}         saved as a shortcut
  press     {key, target?}                Enter, Tab, Escape, Delete, arrows...
  wait      {seconds}
  open_url  {url}

Selectors per target, most robust first: automation id, role + name, visible name, a screen-text (OCR) hint, and
the screen position last (backup only).

Risk: a click or key on something named send / buy / pay / delete / post / publish / submit..., Enter in a text
field (it may send what you typed), send-style shortcuts (Ctrl+Enter) and the Delete key outside a text field
always wait for the user's OK, every time (skill_replay.SkillRunner).
"""
from __future__ import annotations

import ast
import json
import re
import uuid
from typing import Any, Optional

from skills_recorder import (RISKY_WORDS, SCHEMA, _slug, card_lines, looks_secret, order_selectors, target_label,
                             variable_kind)

DESKTOP_ACTIONS = {"focus", "click", "type", "shortcut", "press", "wait", "open_url"}
TEXT_ROLES = {"textbox", "combobox", "spinbutton", "terminal"}
SEND_SHORTCUTS = {("ctrl", "enter"), ("cmd", "enter"), ("ctrl", "return"), ("ctrl", "shift", "enter"),
                  ("alt", "s"), ("ctrl", "shift", "return")}
DELETE_SHORTCUTS = {("shift", "delete"), ("ctrl", "shift", "delete")}
ROLE_WORD = {"button": "button", "link": "link", "tab": "tab", "menuitem": "menu item", "checkbox": "box",
             "radio": "option", "textbox": "box", "combobox": "list", "listitem": "item", "treeitem": "item",
             "icon": "icon", "cell": "cell", "spinbutton": "box", "slider": "slider"}
MOD_ORDER = ("ctrl", "cmd", "alt", "shift", "super")
HUD_APP = re.compile(r"(^orion$|ai\.orion\.(hud|desktop)|^orion hud)", re.I)


def is_desktop(step_or_skill: dict[str, Any]) -> bool:
    return (step_or_skill or {}).get("surface") == "desktop"


NICE_KEY = {"ctrl": "Ctrl", "alt": "Alt", "shift": "Shift", "super": "Super", "cmd": "Cmd", "enter": "Enter",
            "delete": "Delete", "tab": "Tab", "escape": "Esc", "backspace": "Backspace", "space": "Space"}


def nice_keys(keys: list[str]) -> str:
    return "+".join(NICE_KEY.get(k, k.upper() if len(k) == 1 else k.title()) for k in keys)


def norm_keys(keys: list[str]) -> list[str]:
    alias = {"control": "ctrl", "ctl": "ctrl", "return": "enter", "win": "super", "meta": "super", "logo": "super",
             "command": "cmd", "option": "alt", "del": "delete", "esc": "escape"}
    ks = [alias.get(str(k).strip().lower(), str(k).strip().lower()) for k in keys if str(k).strip()]
    mods = [m for m in MOD_ORDER if m in ks]
    rest = [k for k in ks if k not in MOD_ORDER]
    return mods + rest


# ---------------------------------------------------------------------------------- risk
def classify_risk(step: dict[str, Any]) -> Optional[str]:
    a = step.get("action")
    t = step.get("target") or {}
    if a in ("click", "press"):
        m = RISKY_WORDS.search(" ".join(str(t.get(k) or "") for k in ("name", "automation_id")))
        if m:
            return f"looks like it will {m.group(1).lower()}"
    if a == "press":
        key = str(step.get("key") or "").lower()
        if key in ("enter", "return") and (t.get("role") in TEXT_ROLES or not t):
            return "may send or submit what you typed"
        if key == "delete" and t.get("role") not in TEXT_ROLES:
            return "will delete"
    if a == "shortcut":
        ks = tuple(norm_keys(step.get("keys") or []))
        if ks in SEND_SHORTCUTS:
            return "looks like it will send"
        if ks in DELETE_SHORTCUTS:
            return "will delete"
    return None


# ---------------------------------------------------------------------------------- plain words
def describe_step(step: dict[str, Any]) -> str:
    a = step.get("action")
    t = step.get("target") or {}
    name = target_label(t)
    what = f"“{name}”" if name else "the highlighted spot"
    where = f" in “{t['window'][:50]}”" if t.get("window") else ""
    role = ROLE_WORD.get(str(t.get("role") or ""), "")
    if a == "focus":
        app = step.get("app") or "the app"
        win = f" — “{str(step.get('window'))[:50]}”" if step.get("window") else ""
        return f"Switch to {app}{win}"
    if a == "click":
        verb = "Double-click" if step.get("double") else ("Right-click" if step.get("button") == "right" else "Click")
        return f"{verb} the {what} {role}{where}".replace("  ", " ").rstrip() if name else f"{verb} the highlighted spot{where}"
    if a == "type":
        into = f" into {what}" if name else ""
        if step.get("ask"):
            return f"Type the private value{into} (Orion asks you each time)"
        v = str(step.get("value") if step.get("value") is not None else "")
        shown = v if re.fullmatch(r"\{\{\w+\}\}", v) else f"“{v[:50]}”"
        return f"Type {shown}{into}"
    if a == "shortcut":
        return f"Press {nice_keys(norm_keys(step.get('keys') or []))}"
    if a == "press":
        key = str(step.get("key") or "").title()
        return f"Press {key} in {what}" if name else f"Press {key}"
    if a == "wait":
        return f"Wait {step.get('seconds', 1)} s"
    if a == "open_url":
        return f"Open {str(step.get('url', ''))[:80]}"
    return str(a)


# ---------------------------------------------------------------------------------- steps
def _key(t: dict[str, Any]) -> str:
    sels = order_selectors((t or {}).get("selectors") or [])
    for s in sels:
        if s["kind"] != "coords":
            return json.dumps([t.get("app", ""), s], sort_keys=True)
    return json.dumps([t.get("app", ""), target_label(t)])


def is_hud(app: str, window: str = "") -> bool:
    return bool(HUD_APP.search(app or "")) or (window or "").strip().lower() == "orion"


def normalize(steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Clean the recorder's steps: drop the HUD (the Stop button), focus clicks before typing, repeated window
    switches; number them and mark the risky ones."""
    out: list[dict[str, Any]] = []
    for s in steps:
        s = {**s, "surface": "desktop"}
        t = s.get("target") or {}
        if is_hud(str(s.get("app") or t.get("app") or ""), str(s.get("window") or t.get("window") or "")):
            continue
        prev = out[-1] if out else None
        a = s.get("action")
        if a == "focus":
            if prev and prev["action"] == "focus":
                out.pop()
            last_focus = next((x for x in reversed(out) if x["action"] == "focus"), None)
            if last_focus and last_focus.get("app") == s.get("app") and last_focus.get("window") == s.get("window"):
                continue
        if a == "type":
            if s.get("value") in ("", None) and not s.get("ask") and not s.get("light"):
                continue
            if prev and prev["action"] == "click" and t and _key(prev.get("target") or {}) == _key(t) \
                    and (prev.get("target") or {}).get("role") in TEXT_ROLES | {"document", ""}:
                out.pop()  # a click into a text box is just focus
            if prev and prev["action"] == "type" and _key(prev.get("target") or {}) == _key(t):
                out.pop()  # keep the final value
        if a == "click" and prev and prev["action"] == "click" and _key(prev.get("target") or {}) == _key(t) \
                and float(s.get("t") or 0) - float(prev.get("t") or 0) < 0.45:
            prev["double"] = True
            continue
        out.append(s)
    while out and out[-1]["action"] in ("focus", "wait"):
        out.pop()
    for i, s in enumerate(out, 1):
        s.pop("t", None)
        s["id"] = f"s{i}"
        if s.get("target"):
            s["target"]["selectors"] = order_selectors(s["target"].get("selectors") or [])
        reason = classify_risk(s)
        s["risky"] = bool(reason)
        if reason:
            s["risk_reason"] = reason
        else:
            s.pop("risk_reason", None)
    return out


def detect_variables(steps: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Typed values that will change next time (versions, dates, file names, search words) -> {{name}}."""
    steps = json.loads(json.dumps(steps))
    variables: list[dict[str, Any]] = []
    by_value: dict[str, str] = {}
    for s in steps:
        if s.get("action") != "type" or not isinstance(s.get("value"), str) or s.get("ask"):
            continue
        t = s.get("target") or {}
        hint = {"role": "searchbox" if "search" in str(t.get("name", "")).lower() else t.get("role", ""),
                "name": t.get("name", "")}
        kind = variable_kind(s["value"], hint)
        if not kind:
            continue
        v = s["value"]
        if v in by_value:
            name = by_value[v]
            next(x for x in variables if x["name"] == name)["used_in"].append(s["id"])
        else:
            base = _slug(target_label(t), kind) if kind == "search" and target_label(t) else kind
            name, n = base, 2
            while any(x["name"] == name for x in variables):
                name, n = f"{base}_{n}", n + 1
            variables.append({"name": name, "label": (target_label(t) or kind.title())[:60], "kind": kind,
                              "example": v, "used_in": [s["id"]]})
            by_value[v] = name
        s["value"] = "{{" + name + "}}"
    return steps, variables


def build_draft(steps: list[dict[str, Any]], *, skipped: int = 0, title: str = "", app: str = "",
                source: str = "desktop-recorder", backend: str = "") -> dict[str, Any]:
    steps = normalize(steps)
    for s in steps:
        if s.get("action") == "type" and isinstance(s.get("value"), str) and looks_secret(s["value"]):
            s["value"], s["ask"] = None, True
    steps, variables = detect_variables(steps)
    first_app = app or next((s.get("app") for s in steps if s.get("action") == "focus"), "") or ""
    draft = {"kind": "skill", "schema": SCHEMA, "surface": "desktop", "draft_id": f"draft-{uuid.uuid4().hex[:8]}",
             "name": f"New skill in {first_app}" if first_app else "New desktop skill",
             "description": title[:120], "voice_triggers": [], "start_url": "", "start_app": first_app,
             "steps": steps, "variables": variables, "skipped_private": skipped, "source": source,
             "identity": backend}
    draft["card"] = card_lines(draft)
    return draft


def step_signature(step: dict[str, Any]) -> str:
    """Pattern spotting key (no typed values): action | app | element name."""
    a = step.get("action", "")
    t = step.get("target") or {}
    if a == "focus":
        return f"focus|{step.get('app', '')}|{step.get('app', '')}"
    if a == "shortcut":
        return f"shortcut|{t.get('app', step.get('app', ''))}|{nice_keys(norm_keys(step.get('keys') or []))}"
    if a == "press":
        return f"press|{t.get('app', '')}|{step.get('key', '')}"
    return f"{a}|{t.get('app', '')}|{target_label(t).lower()}"


def light_step(step: dict[str, Any]) -> dict[str, Any]:
    """What the activity log keeps of a step: identity only, never typed values or positions."""
    out = {k: step[k] for k in ("action", "app", "window", "keys", "key", "button", "double") if k in step}
    out["surface"] = "desktop"
    t = step.get("target") or {}
    if t:
        lt = {k: t[k] for k in ("app", "window", "role", "name", "automation_id") if t.get(k)}
        lt["selectors"] = [s for s in t.get("selectors") or [] if s.get("kind") in ("automation_id", "role", "text")]
        out["target"] = lt
    if step.get("action") == "type":
        out["value"] = None
        out["light"] = True
    return out


# ---------------------------------------------------------------------------------- Python export
def _q(s: Any) -> str:
    return json.dumps(str(s if s is not None else ""), ensure_ascii=False)


def _const(name: str) -> str:
    return re.sub(r"[^A-Z0-9_]", "_", name.upper())


def _val(template: str) -> str:
    parts = re.split(r"(\{\{\w+\}\})", str(template))
    exprs = []
    for p in parts:
        if not p:
            continue
        m = re.fullmatch(r"\{\{(\w+)\}\}", p)
        exprs.append(_const(m.group(1)) if m else _q(p))
    return " + ".join(exprs) or '""'


PY_HELPERS = '''
import subprocess
import sys
import time


def confirm(what):
    if input(f"Orion: about to {what}. Type yes to go ahead: ").strip().lower() != "yes":
        sys.exit("Stopped before: " + what)


def _gui():
    try:
        import pyautogui
        return pyautogui
    except Exception:
        return None


def _find(role, name, window):
    """Find the element by its accessibility name (UI Automation on Windows, AT-SPI on Linux); None if not found."""
    try:
        if sys.platform == "win32":
            import uiautomation as auto
            c = auto.Control(searchDepth=25, Name=name)
            if c.Exists(3):
                r = c.BoundingRectangle
                return (r.left + r.right) // 2, (r.top + r.bottom) // 2
        else:
            import pyatspi
            desk = pyatspi.Registry.getDesktop(0)
            for app in desk:
                for win in app or []:
                    if window and win and win.name != window:
                        continue
                    hit = pyatspi.findDescendant(win, lambda a: a and a.name == name, breadthFirst=True) if win else None
                    if hit:
                        e = hit.queryComponent().getExtents(pyatspi.DESKTOP_COORDS)
                        return e.x + e.width // 2, e.y + e.height // 2
    except Exception:
        pass
    return None


def focus(app, window=""):
    if sys.platform.startswith("linux"):
        subprocess.run(["swaymsg", f'[title="{window}"] focus'] if window else ["true"], capture_output=True)
    time.sleep(0.5)


def click(role, name, window="", x=None, y=None, button="left", double=False):
    pos = _find(role, name, window) if name else None
    pos = pos or ((x, y) if x is not None else None)
    if pos is None:
        sys.exit(f"Couldn't find {name!r}. Click it yourself and run again from the next step.")
    g = _gui()
    if g is None:
        sys.exit("Install pyautogui to replay clicks.")
    g.click(pos[0], pos[1], clicks=2 if double else 1, button=button)
    time.sleep(0.3)


def type_into(role, name, text, window=""):
    if name:
        click(role, name, window)
    g = _gui()
    if g is not None:
        g.write(text, interval=0.02)
    else:
        subprocess.run(["wtype", text])


def shortcut(*keys):
    g = _gui()
    if g is not None:
        g.hotkey(*keys)
    else:
        subprocess.run(["wtype"] + [a for k in keys[:-1] for a in ("-M", k)] + ["-k", keys[-1]])


def press(key):
    shortcut(key)


def wait(seconds):
    time.sleep(seconds)


def open_url(url):
    import webbrowser
    webbrowser.open(url)
'''


def export_python(skill: dict[str, Any]) -> str:
    """A plain Python script anyone can read and run. Steps that send, buy, delete, post or submit ask first."""
    name = str(skill.get("name") or "skill")
    out = ['"""Orion desktop skill: ' + name.replace('"', "'") + f' (version {skill.get("version", 1)}).',
           "Exported from Orion. Clicks find elements by name first; the screen position is only a backup.",
           "Steps that send, buy, delete, post or submit stop and ask you first.", '"""']
    out += PY_HELPERS.strip("\n").splitlines()
    out.append("")
    variables = skill.get("variables") or []
    if variables:
        out.append("# Values that change each time (pass new ones as NAME=value arguments)")
        for v in variables:
            out.append(f"{_const(v['name'])} = {_q(v.get('example', ''))}  # {v.get('label', '')}")
        out += ["for _arg in sys.argv[1:]:", '    _k, _, _v = _arg.partition("=")',
                "    if _k.upper() in globals():", "        globals()[_k.upper()] = _v"]
    out += ["", "", "def run():"]
    body = 0
    for i, s in enumerate(skill.get("steps") or [], 1):
        out.append(f"    # {i}. {describe_step(s)}")
        if s.get("risky"):
            out.append(f"    confirm({_q(describe_step(s) + ' (' + str(s.get('risk_reason', '')) + ')')})")
        t = s.get("target") or {}
        role, nm, win = _q(t.get("role", "")), _q(t.get("name", "")), _q(t.get("window", ""))
        xy = next(((c.get("x"), c.get("y")) for c in t.get("selectors") or [] if c.get("kind") == "coords"), None)
        a = s.get("action")
        body += 1
        if a == "focus":
            out.append(f"    focus({_q(s.get('app', ''))}, {_q(s.get('window', ''))})")
        elif a == "click":
            extra = f", x={int(xy[0])}, y={int(xy[1])}" if xy else ""
            if s.get("button") == "right":
                extra += ', button="right"'
            if s.get("double"):
                extra += ", double=True"
            out.append(f"    click({role}, {nm}, {win}{extra})")
        elif a == "type":
            val = f'input("Value for {target_label(t) or "this field"}: ")' if s.get("ask") else _val(s.get("value") or "")
            out.append(f"    type_into({role}, {nm}, {val}, {win})")
        elif a == "shortcut":
            out.append("    shortcut(" + ", ".join(_q(k) for k in norm_keys(s.get("keys") or [])) + ")")
        elif a == "press":
            out.append(f"    press({_q(str(s.get('key', '')).lower())})")
        elif a == "wait":
            out.append(f"    wait({float(s.get('seconds') or 1)})")
        elif a == "open_url":
            out.append(f"    open_url({_val(s.get('url', ''))})")
        else:
            body -= 1
            out.append("    pass")
    if not body:
        out.append("    pass")
    out += ["", "", 'if __name__ == "__main__":', "    run()", ""]
    return "\n".join(out)


# ---------------------------------------------------------------------------------- Python import
IMPORT_LIMITS = ("Python import reads the script without running it. It understands Orion's own exported "
                 "scripts (focus, click, type_into, shortcut, press, wait, open_url), pyautogui (click, doubleClick, "
                 "rightClick, write/typewrite, hotkey, press), keyboard.write / send / press_and_release, "
                 "time.sleep, webbrowser.open, opening an app with subprocess or os.startfile, and uiautomation "
                 "(XxxControl(Name=...).Click() / SendKeys() / SetActive()). Loops, conditions, variables computed "
                 "at run time and anything else are skipped and listed so you can record those parts instead.")
UIA_ROLE = re.compile(r"^(\w+?)Control$")


def _lit(node: Optional[ast.AST]) -> Any:
    if node is None:
        return None
    try:
        return ast.literal_eval(node)
    except Exception:
        return None


def _dotted(fn: ast.AST) -> str:
    parts = []
    while isinstance(fn, ast.Attribute):
        parts.append(fn.attr)
        fn = fn.value
    if isinstance(fn, ast.Name):
        parts.append(fn.id)
    return ".".join(reversed(parts))


def _kw(call: ast.Call, name: str) -> Any:
    for k in call.keywords:
        if k.arg == name:
            return _lit(k.value)
    return None


def _arg(call: ast.Call, i: int, kw: str = "") -> Any:
    if len(call.args) > i:
        return _lit(call.args[i])
    return _kw(call, kw) if kw else None


def _target(role: str, name: str, window: str = "", x: Any = None, y: Any = None) -> dict[str, Any]:
    t: dict[str, Any] = {k: v for k, v in (("role", role), ("name", name), ("window", window)) if v}
    sels: list[dict[str, Any]] = []
    if role and name:
        sels.append({"kind": "role", "role": role, "name": name})
    if name:
        sels.append({"kind": "text", "value": name})
    if isinstance(x, (int, float)) and isinstance(y, (int, float)):
        sels.append({"kind": "coords", "x": int(x), "y": int(y)})
    t["selectors"] = sels
    return t


def _app_from_command(cmd: Any) -> Optional[str]:
    if isinstance(cmd, (list, tuple)) and cmd and all(isinstance(c, str) for c in cmd):
        cmd = cmd[0]
    if not isinstance(cmd, str) or re.search(r"[;&|`$<>]", cmd) or len(cmd.split()) > 1:
        return None
    base = re.split(r"[\\/]", cmd)[-1]
    return re.sub(r"\.exe$", "", base, flags=re.I) or None


def import_python(code: str, name: str = "Imported skill") -> tuple[dict[str, Any], list[str]]:
    """Plain Python automation script -> desktop skill draft. The script is parsed, never run."""
    skipped: list[str] = []
    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        draft = build_draft([], source="python")
        draft.update(name=name, import_skipped=[f"line {exc.lineno}: the script doesn't parse"],
                     import_limits=IMPORT_LIMITS)
        return draft, draft["import_skipped"]
    steps: list[dict[str, Any]] = []
    t = 0.0
    defined = {n.name for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    helper_names = {"confirm", "_gui", "_find", "focus", "click", "type_into", "shortcut", "press", "wait",
                    "open_url"}
    orion_script = helper_names <= defined

    def body_of(stmts: list[ast.stmt]) -> None:
        for st in stmts:
            if isinstance(st, (ast.Import, ast.ImportFrom)):
                continue
            if isinstance(st, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if orion_script and st.name in helper_names:
                    continue
                if st.name in ("run", "main"):
                    body_of(st.body)
                elif st.name not in called:
                    continue
                else:
                    body_of(st.body)
                continue
            if isinstance(st, ast.If) and "__name__" in ast.unparse(st.test):
                continue  # the entry point only calls run()/main()
            if orion_script and isinstance(st, ast.For) and "sys.argv" in ast.unparse(st.iter):
                continue  # the exported NAME=value argument parser
            if isinstance(st, (ast.For, ast.While, ast.If, ast.Try, ast.With, ast.AsyncWith, ast.AsyncFor)):
                if isinstance(st, (ast.With, ast.AsyncWith)):
                    body_of(st.body)
                    continue
                skipped.append(f"line {st.lineno}: {ast.unparse(st).splitlines()[0][:100]}")
                continue
            if isinstance(st, ast.Expr) and isinstance(st.value, ast.Call):
                if not handle(st.value):
                    skipped.append(f"line {st.lineno}: {ast.unparse(st)[:100]}")
                continue
            if isinstance(st, (ast.Assign, ast.AnnAssign)) and isinstance(getattr(st, "value", None), ast.Constant):
                continue  # NAME = "value" constants (exported variables)
            if isinstance(st, ast.Expr) and isinstance(st.value, ast.Constant):
                continue  # docstring
            skipped.append(f"line {st.lineno}: {ast.unparse(st).splitlines()[0][:100]}")

    consts: dict[str, str] = {}
    for st in tree.body:
        if isinstance(st, ast.Assign) and len(st.targets) == 1 and isinstance(st.targets[0], ast.Name) \
                and isinstance(st.value, ast.Constant) and isinstance(st.value.value, str):
            consts[st.targets[0].id] = st.value.value
    called = {_dotted(n.func) for n in ast.walk(tree) if isinstance(n, ast.Call)}

    def strval(node: Optional[ast.AST]) -> Optional[str]:
        """A literal string, or NAME / "a" + NAME made of module constants -> template with {{name}}."""
        if node is None:
            return None
        v = _lit(node)
        if isinstance(v, str):
            return v
        if isinstance(node, ast.Name) and node.id in consts:
            return "{{" + node.id.lower() + "}}"
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            a, b = strval(node.left), strval(node.right)
            return a + b if a is not None and b is not None else None
        return None

    def add(step: dict[str, Any]) -> bool:
        nonlocal t
        t += 1.0
        steps.append({**step, "surface": "desktop", "t": t})
        return True

    def handle(call: ast.Call) -> bool:
        fn = _dotted(call.func)
        last = fn.split(".")[-1]
        mod = fn.split(".")[0] if "." in fn else ""
        # Orion's own exported helpers
        if orion_script or not mod:
            if last == "confirm":
                return True
            if last == "focus" and not mod:
                return add({"action": "focus", "app": _arg(call, 0, "app") or "", "window": _arg(call, 1, "window") or ""})
            if last == "click" and not mod:
                tg = _target(_arg(call, 0, "role") or "", _arg(call, 1, "name") or "", _arg(call, 2, "window") or "",
                             _kw(call, "x"), _kw(call, "y"))
                st: dict[str, Any] = {"action": "click", "target": tg}
                if _kw(call, "button") == "right":
                    st["button"] = "right"
                if _kw(call, "double"):
                    st["double"] = True
                return add(st)
            if last == "type_into" and not mod:
                val = strval(call.args[2]) if len(call.args) > 2 else None
                tg = _target(_arg(call, 0, "role") or "", _arg(call, 1, "name") or "", _arg(call, 3, "window") or "")
                if val is None:
                    return add({"action": "type", "target": tg, "value": None, "ask": True})
                return add({"action": "type", "target": tg, "value": val})
            if last == "shortcut" and not mod:
                return add({"action": "shortcut", "keys": norm_keys([_lit(a) for a in call.args if isinstance(_lit(a), str)])})
            if last == "press" and not mod:
                return add({"action": "press", "key": str(_arg(call, 0, "key") or "")})
            if last == "wait" and not mod:
                return add({"action": "wait", "seconds": float(_arg(call, 0, "seconds") or 1)})
            if last == "open_url" and not mod:
                return add({"action": "open_url", "url": strval(call.args[0]) if call.args else ""})
        if mod in ("pyautogui", "pag", "gui"):
            if last in ("click", "doubleClick", "rightClick", "leftClick"):
                x, y = _arg(call, 0, "x"), _arg(call, 1, "y")
                if not isinstance(x, (int, float)):
                    return False
                st = {"action": "click", "target": _target("", "", "", x, y)}
                if last == "doubleClick" or (_kw(call, "clicks") or 1) == 2:
                    st["double"] = True
                if last == "rightClick" or _kw(call, "button") == "right":
                    st["button"] = "right"
                return add(st)
            if last in ("write", "typewrite"):
                val = strval(call.args[0]) if call.args else None
                return val is not None and add({"action": "type", "value": val})
            if last == "hotkey":
                ks = [_lit(a) for a in call.args]
                return all(isinstance(k, str) for k in ks) and bool(ks) and add({"action": "shortcut", "keys": norm_keys(ks)})
            if last == "press":
                k = _arg(call, 0, "keys")
                return isinstance(k, str) and add({"action": "press", "key": k})
            if last in ("moveTo", "sleep", "PAUSE"):
                return True
        if mod == "keyboard":
            if last == "write":
                val = strval(call.args[0]) if call.args else None
                return val is not None and add({"action": "type", "value": val})
            if last in ("send", "press_and_release"):
                k = _arg(call, 0)
                if not isinstance(k, str):
                    return False
                ks = norm_keys(k.split("+"))
                return add({"action": "shortcut", "keys": ks} if len(ks) > 1 else {"action": "press", "key": ks[0]})
        if fn in ("time.sleep", "sleep"):
            s = _arg(call, 0)
            return isinstance(s, (int, float)) and add({"action": "wait", "seconds": float(s)})
        if fn == "webbrowser.open":
            url = strval(call.args[0]) if call.args else None
            return bool(url) and add({"action": "open_url", "url": url})
        if fn in ("subprocess.Popen", "subprocess.run", "subprocess.call", "os.startfile", "os.system"):
            app = _app_from_command(_arg(call, 0))
            return bool(app) and add({"action": "focus", "app": app, "window": ""})
        # uiautomation: auto.ButtonControl(Name="Save").Click()
        if isinstance(call.func, ast.Attribute) and isinstance(call.func.value, ast.Call):
            inner = call.func.value
            m = UIA_ROLE.match(_dotted(inner.func).split(".")[-1])
            if m:
                import desktop_access
                role = desktop_access.norm_role(m.group(1))
                nm = _kw(inner, "Name") or ""
                tg = _target(role, nm)
                if _kw(inner, "AutomationId"):
                    tg["automation_id"] = _kw(inner, "AutomationId")
                    tg["selectors"].insert(0, {"kind": "automation_id", "value": tg["automation_id"]})
                verb = call.func.attr
                if role == "window" and verb in ("SetActive", "SetFocus", "SetTopmost"):
                    return add({"action": "focus", "app": "", "window": nm})
                if verb in ("Click", "DoubleClick", "RightClick", "Invoke"):
                    st = {"action": "click", "target": tg}
                    if verb == "DoubleClick":
                        st["double"] = True
                    if verb == "RightClick":
                        st["button"] = "right"
                    return add(st)
                if verb in ("SendKeys", "SetValue"):
                    val = strval(call.args[0]) if call.args else None
                    return val is not None and add({"action": "type", "target": tg, "value": val})
        return False

    body_of(tree.body)
    draft = build_draft(steps, source="python")
    # constants used as {{name}} become variables with their default as the example
    for cname, value in consts.items():
        vname = cname.lower()
        if any(vname in json.dumps(s) for s in draft["steps"]) and not any(v["name"] == vname for v in draft["variables"]):
            draft["variables"].append({"name": vname, "label": vname.replace("_", " ").title(), "kind": "text",
                                       "example": value, "used_in": [s["id"] for s in draft["steps"]
                                                                     if "{{" + vname + "}}" in json.dumps(s)]})
    draft["name"] = name
    draft["import_skipped"] = skipped
    draft["import_limits"] = IMPORT_LIMITS
    draft["card"] = card_lines(draft)
    return draft, skipped


def looks_like_playwright(code: str) -> bool:
    return bool(re.search(r"(playwright|\bpage\.\w+\(|getByRole|chromium\.launch)", code))
