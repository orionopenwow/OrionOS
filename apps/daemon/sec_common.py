"""Small shared helpers for the security toolkit tools (17 Dependency Autopilot, 19 Supply-Chain Scan,
20 Exposure Map, 21 Host Health). Everything that touches the outside world goes through `Shell`, so tests swap
in a fake that answers from fixture files."""
from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any, Callable, Optional, Sequence

log = logging.getLogger("orion.security")

MAX_OUT = 4_000_000


class Shell:
    """run(argv) -> (exit code, stdout+stderr text); which(name) -> path or None. Never raises."""

    def which(self, name: str) -> Optional[str]:
        env = os.environ.get("ORION_" + name.upper().replace("-", "_") + "_BIN")
        if env:
            return env if os.access(env, os.X_OK) else None
        return shutil.which(name)

    def run(self, argv: Sequence[str], cwd: Optional[str] = None, timeout: float = 300,
            env: Optional[dict[str, str]] = None, stderr: bool = True) -> tuple[int, str]:
        try:
            p = subprocess.run(list(argv), cwd=cwd, capture_output=True, timeout=timeout,
                               env=env if env is not None else {**os.environ, "LC_ALL": "C", "NO_COLOR": "1"})
        except subprocess.TimeoutExpired:
            return 124, "timed out"
        except OSError as exc:
            return 127, str(exc)
        out = p.stdout.decode("utf-8", "replace")
        if stderr:
            out += p.stderr.decode("utf-8", "replace")
        return p.returncode, out[-MAX_OUT:]

    def read(self, path: str) -> Optional[str]:
        try:
            return Path(path).read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None


def read_json(p: Path, default: Any = None) -> Any:
    try:
        return json.loads(Path(p).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def write_json(p: Path, data: Any) -> None:
    try:
        p = Path(p)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(p.suffix + ".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(p)
    except OSError as exc:
        log.warning("Could not save %s: %s", p, exc)


def loads(text: str, default: Any = None) -> Any:
    """JSON from a tool's output, skipping any log lines printed before it."""
    text = (text or "").strip()
    if not text:
        return default
    try:
        return json.loads(text)
    except ValueError:
        pass
    for opener in ("{", "["):
        i = text.find("\n" + opener)
        start = i + 1 if i >= 0 else (0 if text.startswith(opener) else -1)
        if start >= 0:
            try:
                return json.loads(text[start:])
            except ValueError:
                continue
    return default


def plural(n: int, word: str, many: str = "") -> str:
    return f"{n} {word if n == 1 else (many or word + 's')}"


def alert(title: str, body: str, ref: str) -> None:
    try:
        import alerts
        alerts.emit("system", title, body, ref=ref)
    except Exception:
        pass


def profile() -> str:
    """vm | baremetal | image | server | '' (not OrionOS)."""
    try:
        return Path("/etc/orion/profile").read_text().strip()
    except OSError:
        return ""


def in_vm(sh: Optional[Shell] = None) -> bool:
    if profile() == "vm":
        return True
    sh = sh or Shell()
    if not sh.which("systemd-detect-virt"):
        return False
    rc, out = sh.run(["systemd-detect-virt", "-q"], timeout=10)
    return rc == 0


def machine_name() -> str:
    try:
        return os.uname().nodename or "this computer"
    except AttributeError:   # Windows
        return os.environ.get("COMPUTERNAME", "this computer")


Notify = Callable[[str, str], None]
