"""The daemon also runs on Windows (Start Orion.cmd). Modules that only exist on Linux (pwd, grp, fcntl, ...) must be
imported inside try/except ImportError, or the whole daemon dies at start-up on Windows. Found 2026-10-18:
everyone.py's bare `import pwd` stopped the daemon from starting on the Windows PC."""
from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

DAEMON = Path(__file__).resolve().parents[1]
POSIX_ONLY = {"pwd", "grp", "fcntl", "termios", "resource", "pty", "tty", "crypt", "spwd", "syslog"}


def _guarded(node: ast.AST, parents: dict[ast.AST, ast.AST]) -> bool:
    p = parents.get(node)
    while p is not None:
        if isinstance(p, ast.Try) and any(
            h.type is None or "ImportError" in ast.unparse(h.type) or "Exception" in ast.unparse(h.type)
            for h in p.handlers
        ):
            return True
        if isinstance(p, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return True          # imported only when called; callers on Windows don't reach it
        p = parents.get(p)
    return False


def test_no_bare_linux_only_imports():
    bad = []
    for f in sorted(DAEMON.glob("*.py")):
        tree = ast.parse(f.read_text(encoding="utf-8"))
        parents = {c: n for n in ast.walk(tree) for c in ast.iter_child_nodes(n)}
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name.split(".")[0] for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module.split(".")[0]]
            if POSIX_ONLY & set(names) and not _guarded(node, parents):
                bad.append(f"{f.name}:{node.lineno}")
    assert not bad, "Linux-only import outside try/except ImportError (breaks Windows): " + ", ".join(bad)


def test_everyone_imports_without_pwd():
    code = ("import sys; sys.modules['pwd'] = None; sys.path.insert(0, %r); import everyone; "
            "assert everyone.guest_mode() is False; print('ok')") % str(DAEMON)
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60,
                       env={"PATH": "", "ORION_GUEST_MODE": ""})
    assert r.returncode == 0 and "ok" in r.stdout, r.stderr[-800:]
