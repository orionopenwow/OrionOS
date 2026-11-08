"""Small fake commands for the OS shell-script tests (no root, no real disks, no network)."""
import os
import sys
from pathlib import Path

EDITENV = r'''
import sys
f, cmd, *rest = sys.argv[1:]
try:
    lines = [l for l in open(f).read().splitlines() if "=" in l]
except OSError:
    lines = []
d = dict(l.split("=", 1) for l in lines)
if cmd == "list":
    print("\n".join(f"{k}={v}" for k, v in d.items()))
elif cmd == "create":
    open(f, "a").close()
elif cmd == "set":
    for kv in rest:
        k, v = kv.split("=", 1); d[k] = v
elif cmd == "unset":
    for k in rest:
        d.pop(k, None)
if cmd in ("set", "unset", "create"):
    open(f, "w").write("".join(f"{k}={v}\n" for k, v in d.items()))
'''


def make(bindir: Path, name: str, body: str, python: bool = True) -> Path:
    bindir.mkdir(parents=True, exist_ok=True)
    p = bindir / name
    if python:
        log = bindir.parent / "calls.txt"
        p.write_text(f"#!{sys.executable}\nimport os, sys\nopen({str(log)!r}, 'a').write({name!r} + ' ' + "
                     f"' '.join(sys.argv[1:]) + '\\n')\nargs = sys.argv[1:]\n" + body)
    else:
        p.write_text(body)
    p.chmod(0o755)
    return p


def calls(bindir: Path) -> list[str]:
    f = bindir.parent / "calls.txt"
    return f.read_text().splitlines() if f.exists() else []


def grubenv(path: Path) -> dict:
    try:
        return dict(l.split("=", 1) for l in path.read_text().splitlines() if "=" in l)
    except OSError:
        return {}


def path_with(bindir: Path) -> str:
    return f"{bindir}:{os.environ.get('PATH', '/usr/bin:/bin')}"
