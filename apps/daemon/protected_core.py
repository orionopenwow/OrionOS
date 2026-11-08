"""Orion's protected core (docs/ORIONOS_AFTER_1_0_PLAN.md §7 item 6).

Some code can't be changed by Orion's self-change flow without your phone's fingerprint or a boardroom signature, and
never automatically: the approval system and the signer, the firewall, the recorder's privacy rules and the
self-change gate itself.

The list lives in two places that are joined: RULES / TAMPER below (code) and protected_core.json (data, easy to read
and review). Deleting or editing the data file can only ever ADD protection, never remove it, and
tests/test_protected_core.py checks the two say the same thing.

    check(files) -> list of hits      files: [{"path", "action": "modify"|"create"|"delete", "before", "after"}]

A rule with `symbols` protects those named parts of a Python file (functions, classes, methods as Class.method, and
module-level names) plus every import line in it; a change anywhere else in that file is allowed. If the file can't
be parsed, or a listed name has gone missing (the list is out of date), the whole file counts as protected: when in
doubt, it asks for the signature. Paths are compared without regard to upper/lower case (Windows file systems).
"""
from __future__ import annotations

import ast
import difflib
import fnmatch
import json
import posixpath
import re
from pathlib import Path
from typing import Any, Iterable, Optional

DATA_FILE = Path(__file__).with_name("protected_core.json")

_BOARD = ["SIG_DOMAIN", "MAX_CLOCK_SKEW_S", "PAIRING_TTL_S", "TaskType", "TASK_TYPES", "fingerprint", "packet_hash",
          "signing_message", "verify_signature", "Boardroom.pubkey", "Boardroom.new_pairing_code", "Boardroom.pair",
          "Boardroom.pending_fingerprint", "Boardroom.confirm_pending", "Boardroom.unpair", "Boardroom.check_request",
          "Boardroom.propose", "Boardroom.verdict", "Boardroom._execute", "build_remote_app"]
_PHONES = ["PAIRING_TTL_S", "MAX_CLOCK_SKEW_S", "fingerprint", "pairing_proof", "_load_p256", "verify_p256",
           "PhoneRegistry.pair", "PhoneRegistry.pending_fingerprint", "PhoneRegistry.confirm", "PhoneRegistry.remove",
           "PhoneRegistry.check"]
_RECORDER = ["looks_secret", "SENSITIVE_PAGE", "CARD_HINT", "CODE_HINT", "PASSWORD_HINT", "sensitive_field_reason",
             "PRIVATE_PARAM", "scrub_url", "page_looks_sensitive", "normalize_events"]

# (path glob, area, why, symbols or None for the whole file)
RULES: list[tuple[str, str, str, Optional[list[str]]]] = [
    ("apps/daemon/approval_queue.py", "approvals", "the approval system", None),
    ("apps/daemon/boardroom.py", "approvals", "the signer: signatures and verdicts", _BOARD),
    ("apps/daemon/phones.py", "approvals", "the phone keys and their checks", _PHONES),
    ("apps/mobile/app/src/main/java/com/orion/mobile/Keys.kt", "approvals", "the phone's signing keys", None),
    ("apps/daemon/api_token.py", "approvals", "the daemon's key", None),
    ("apps/desktop/src-tauri/tauri.conf.json", "approvals", "what the desktop app may do on your PC", None),
    ("apps/desktop/src-tauri/capabilities/*", "approvals", "what the desktop app may do on your PC", None),
    ("apps/daemon/security.py", "approvals", "who may talk to the daemon", None),
    ("apps/daemon/main.py", "approvals", "the daemon's key check and health answer",
     ["reject_foreign_origins", "health"]),
    ("os/common/harden.sh", "firewall", "the firewall rules", None),
    ("os/common/*.nft*", "firewall", "the firewall rules", None),
    ("apps/daemon/injection_firewall.py", "firewall", "the injection firewall", None),
    ("apps/daemon/capture_privacy.py", "privacy", "the recorder's privacy rules", None),
    ("apps/daemon/skills_recorder.py", "privacy", "the recorder's private-field rules", _RECORDER),
    ("apps/extension/recorder.js", "privacy", "the browser recorder's skip rules", None),
    ("apps/daemon/self_evolve.py", "self-change gate", "the self-change gate", None),
    ("apps/daemon/self_program.py", "self-change gate", "the self-change gate", None),
    ("apps/daemon/protected_core.py", "self-change gate", "the protected-core list", None),
    ("apps/daemon/protected_core.json", "self-change gate", "the protected-core list", None),
    ("apps/daemon/orion_layer.py", "self-change gate", "undo and the start-up guard", None),
    ("apps/daemon/self_levels.py", "self-change gate", "the permission levels and their ceilings", None),
    ("apps/daemon/freeze.py", "self-change gate", "the kill switch", None),
    ("os/safety/orion-self-gate", "self-change gate", "the system's self-change gate", None),
    ("os/lab/bin/orion-self-gate", "self-change gate", "the system's self-change gate", None),
    ("scripts/run-daemon.mjs", "self-change gate", "restart and automatic undo", None),
    ("apps/daemon/tests/test_protected_core.py", "self-change gate", "the proof the gate holds", None),
    ("apps/daemon/tests/test_self_program.py", "self-change gate", "the proof the gate holds", None),
]

_MODS = ("approval_queue|boardroom|phones|api_token|security|injection_firewall|capture_privacy|skills_recorder|"
         "self_evolve|self_program|protected_core|orion_layer|self_levels|freeze")
TAMPER: list[tuple[str, str]] = [
    (rf"(?<![\w.])({_MODS})\.[A-Za-z_][A-Za-z0-9_]*\s*=(?!=)", "replaces part of the protected core from outside"),
    (rf"setattr\(\s*({_MODS})\b", "replaces part of the protected core from outside"),
    (r"\.orion-signing|protected_core\.json|self-levels\.json|self_levels\.json|probation\.json",
     "touches the signer's keys or the gate's own files"),
    (r"\borion_layer\.(set_gen|rollback|set_probation|save_history|update_swap|new_gen)\b", "changes the undo history"),
    (r"ORION_LAYER_MODE|ORION_SELF_LEVELS_ADMIN|ORION_FREEZE_MARK", "changes how the gate is set up"),
]
# Tests only ever run inside the sandbox copy, so they may poke at the core (they are still path-protected above).
TAMPER_EXEMPT = ("apps/daemon/tests/",)


# =====================================================================================  the joined list
def _data() -> dict[str, Any]:
    try:
        d = json.loads(DATA_FILE.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def rules() -> list[dict[str, Any]]:
    """Code rules joined with the data file. For the same path, symbol lists are merged; a whole-file rule wins."""
    out: dict[str, dict[str, Any]] = {}

    def add(path: str, area: str, why: str, symbols: Optional[Iterable[str]]) -> None:
        key = path.replace("\\", "/").lower()
        r = out.get(key)
        syms = None if symbols is None else sorted({str(s) for s in symbols})
        if r is None:
            out[key] = {"path": path, "area": area, "why": why, "symbols": syms}
        elif r["symbols"] is not None:
            r["symbols"] = None if syms is None else sorted(set(r["symbols"]) | set(syms))

    for path, area, why, syms in RULES:
        add(path, area, why, syms)
    for r in _data().get("rules", []) or []:
        if isinstance(r, dict) and isinstance(r.get("path"), str):
            s = r.get("symbols")
            add(r["path"], str(r.get("area", "")), str(r.get("why", "protected")),
                [x for x in s if isinstance(x, str)] if isinstance(s, list) else None)
    return list(out.values())


def tamper_patterns() -> list[tuple[re.Pattern, str]]:
    pats = list(TAMPER)
    for t in _data().get("tamper", []) or []:
        if isinstance(t, dict) and isinstance(t.get("pattern"), str):
            pats.append((t["pattern"], str(t.get("why", "reaches into the protected core"))))
    out = []
    for p, why in pats:
        try:
            out.append((re.compile(p), why))
        except re.error:
            continue
    return out


def norm_path(path: str) -> str:
    """One spelling per file, so "apps/daemon/./safety.py", "apps//daemon/safety.py" or "apps\\daemon\\safety.py"
    can't slip past the rules (the file written is the same one)."""
    rel = posixpath.normpath(str(path).replace("\\", "/")).lstrip("/")
    return "" if rel == "." else rel


def rule_for(path: str) -> Optional[dict[str, Any]]:
    rel = norm_path(path).lower()
    for r in rules():
        if fnmatch.fnmatchcase(rel, r["path"].lower()):
            return r
    return None


def is_protected_path(path: str) -> bool:
    return rule_for(path) is not None


# =====================================================================================  symbol ranges
def _ranges(src: str) -> Optional[dict[str, list[tuple[int, int]]]]:
    """name -> [(first line, last line)] for module-level bindings and class methods; '<imports>' for imports."""
    try:
        tree = ast.parse(src)
    except (SyntaxError, ValueError):
        return None
    out: dict[str, list[tuple[int, int]]] = {}

    def put(name: str, node: ast.AST) -> None:
        start = min([node.lineno] + [d.lineno for d in getattr(node, "decorator_list", [])])
        out.setdefault(name, []).append((start, getattr(node, "end_lineno", node.lineno) or node.lineno))

    def target_names(t: ast.AST) -> list[str]:
        if isinstance(t, ast.Name):
            return [t.id]
        if isinstance(t, ast.Attribute) and isinstance(t.value, ast.Name):
            return [f"{t.value.id}.{t.attr}"]
        if isinstance(t, (ast.Tuple, ast.List)):
            return [n for e in t.elts for n in target_names(e)]
        return []

    def walk(body: list[ast.stmt]) -> None:
        for node in body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                put(node.name, node)
            elif isinstance(node, ast.ClassDef):
                put(node.name, node)
                for sub in node.body:
                    if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                        put(f"{node.name}.{sub.name}", sub)
                    elif isinstance(sub, (ast.Assign, ast.AnnAssign)):
                        for t in (sub.targets if isinstance(sub, ast.Assign) else [sub.target]):
                            for n in target_names(t):
                                put(f"{node.name}.{n}", sub)
            elif isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
                for t in (node.targets if isinstance(node, ast.Assign) else [node.target]):
                    for n in target_names(t):
                        put(n, node)
            elif isinstance(node, (ast.Import, ast.ImportFrom)):
                put("<imports>", node)
                for a in node.names:
                    put((a.asname or a.name).split(".")[0], node)
            elif isinstance(node, (ast.If, ast.Try, ast.With)):
                for part in ("body", "orelse", "finalbody"):
                    walk(getattr(node, part, []) or [])
                for h in getattr(node, "handlers", []) or []:
                    walk(h.body)
            elif isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
                f = node.value.func                                 # setattr(Boardroom, "verdict", ...)
                if isinstance(f, ast.Name) and f.id == "setattr" and node.value.args:
                    a0 = node.value.args[0]
                    a1 = node.value.args[1] if len(node.value.args) > 1 else None
                    if isinstance(a0, ast.Name):
                        name = a0.id
                        if isinstance(a1, ast.Constant) and isinstance(a1.value, str):
                            name = f"{a0.id}.{a1.value}"
                        put(name, node)
    walk(tree.body)
    return out


def _changed_lines(before: str, after: str) -> tuple[set[int], set[int], list[int]]:
    """1-based changed lines in `before`, in `after`, and insertion points in `before` (insert before line k)."""
    a, b = before.splitlines(), after.splitlines()
    old, new, ins = set(), set(), []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if tag == "equal":
            continue
        old.update(range(i1 + 1, i2 + 1))
        new.update(range(j1 + 1, j2 + 1))
        if i1 == i2:
            ins.append(i1 + 1)
    return old, new, ins


def _symbol_hits(symbols: list[str], before: str, after: str) -> Optional[list[str]]:
    """Which protected names the change touches. None means 'treat the whole file as protected'."""
    rb, ra = _ranges(before), _ranges(after)
    if rb is None or ra is None:
        return None
    if any(s not in rb for s in symbols):
        return None                                     # the list is out of date: fail closed
    old, new, ins = _changed_lines(before, after)
    hit: set[str] = set()
    for name in list(symbols) + ["<imports>"]:
        for (s, e) in rb.get(name, []):
            if any(s <= ln <= e for ln in old) or any(s < k <= e for k in ins):
                hit.add(name)
        for (s, e) in ra.get(name, []):
            if any(s <= ln <= e for ln in new):
                hit.add(name)
    # a protected name re-bound or replaced anywhere else in the new file (e.g. "verify_signature = ...")
    for name in symbols:
        if len(ra.get(name, [])) > len(rb.get(name, [])):
            hit.add(name)
    return sorted(hit)


# =====================================================================================  the check
def check(files: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Every way this change touches the protected core. Empty list = not protected."""
    hits: list[dict[str, Any]] = []
    pats = tamper_patterns()
    for f in files:
        rel = norm_path(str(f.get("path", "")))
        action = f.get("action") or "modify"
        before = f.get("before") or ""
        after = f.get("after") or ""
        r = rule_for(rel)
        if r is not None:
            if r["symbols"] is None or action != "modify":
                hits.append({"path": rel, "area": r["area"], "why": r["why"], "parts": []})
            else:
                parts = _symbol_hits(r["symbols"], before, after)
                if parts is None:
                    hits.append({"path": rel, "area": r["area"], "why": r["why"] + " (couldn't tell which part)",
                                 "parts": []})
                elif parts:
                    hits.append({"path": rel, "area": r["area"], "why": r["why"], "parts": parts})
        if rel.lower().startswith(TAMPER_EXEMPT) or action == "delete":
            continue
        _, new_lines, _ = _changed_lines(before, after)
        lines = after.splitlines()
        added = "\n".join(lines[i - 1] for i in sorted(new_lines) if 0 < i <= len(lines))
        for rx, why in pats:
            if rx.search(added):
                hits.append({"path": rel, "area": "tamper", "why": why, "parts": []})
                break
    return hits


def describe(hits: list[dict[str, Any]]) -> str:
    if not hits:
        return ""
    seen = []
    for h in hits:
        w = f"{h['path']} ({h['why']})"
        if w not in seen:
            seen.append(w)
    return "This change touches Orion's protected core: " + "; ".join(seen[:6]) + \
        ". It needs your phone's fingerprint or a boardroom signature."
