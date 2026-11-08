"""A fake Shell for the security toolkit tests: answers commands from fixture files."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Optional, Sequence

from sec_common import Shell

FIX = Path(__file__).resolve().parent / "fixtures" / "security"


def fixture(name: str) -> str:
    return (FIX / name).read_text(encoding="utf-8")


class FakeShell(Shell):
    """runs: {"cmd sub": (rc, text)} matched against the start of ' '.join(argv) (tool path stripped to its
    name); tools: names that `which` finds; files: path -> text for read(). `real` commands (git) really run."""

    def __init__(self, runs: Optional[dict[str, Any]] = None, tools: Sequence[str] = (),
                 files: Optional[dict[str, str]] = None, real: Sequence[str] = ("git",)) -> None:
        self.runs = runs or {}
        self.tools = set(tools)
        self.files = files or {}
        self.real = set(real)
        self.calls: list[list[str]] = []

    def which(self, name: str) -> Optional[str]:
        return f"/usr/bin/{name}" if name in self.tools else None

    def run(self, argv: Sequence[str], cwd: Optional[str] = None, timeout: float = 300,
            env: Optional[dict[str, str]] = None, stderr: bool = True) -> tuple[int, str]:
        argv = list(argv)
        name = Path(argv[0]).name
        self.calls.append([name, *argv[1:]])
        if name in self.real:
            return super().run(argv, cwd, timeout, env, stderr)
        line = " ".join([name, *argv[1:]])
        for prefix in sorted(self.runs, key=len, reverse=True):
            if line.startswith(prefix):
                ans = self.runs[prefix]
                return ans(argv, cwd) if callable(ans) else ans
        return 127, f"{name}: not faked"

    def read(self, path: str) -> Optional[str]:
        if path in self.files:
            return self.files[path]
        if path.startswith(str(FIX)):
            return super().read(path)
        return None
