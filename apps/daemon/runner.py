"""Self-Healing Terminal Wrapper (Feature Bible D1).

Every shell command Orion runs — Grimoire scripts, Battle Station dev servers, fix commands — goes
through here. Output is streamed live to the HUD; when a command or a long-running service crashes,
Orion captures the tail of stdout/stderr, diagnoses it (instant heuristics for the classics — port in
use, missing Python/Node module, missing command — then the LLM), and offers a one-click
[Fix & Restart].
"""
from __future__ import annotations

import asyncio
import collections
import json
import logging
import os
import re
import sys
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Optional

log = logging.getLogger("orion.runner")

Publish = Callable[[dict[str, Any]], Awaitable[None]]
TAIL_LINES = 200
# Commands that delete, overwrite, force-push, wipe disks, change system state or pipe downloaded code into a shell
# need the user's OK first (callers check is_destructive). This is a speed bump, not a sandbox: commands are matched
# after undoing the usual tricks (quotes, backslashes, cmd ^ escapes, ${IFS}, extra spaces), and anything that hides
# what it runs (eval, base64 into a shell, $(curl ...)) counts as destructive. Review item: runner.py shell hardening.
DESTRUCTIVE = re.compile(
    r"(\brm\s+(-[a-z]*[rf]|--recursive|--force|--no-preserve-root)|\brmdir\b|\bunlink\s|\bshred\b|\bwipefs\b|"
    r"\bfind\b[^|;&]*\s-(delete|exec\s+rm)\b|\bxargs\s+(-\S+\s+)*rm\b|\btruncate\s+-s|"
    r"\bdel\s+/[sqf]|\berase\s+/[sqf]|\brd\s+/s|\brmdir\s+/s|"
    r"\b(remove-item|ri|rm|del|erase|rd|rmdir)\b[^|;&]*\s-(r|rec|recurse|force)\b|clear-content\b|clear-disk\b|"
    r"format-volume\b|\bformat\s+[a-z]:|\bdiskpart\b|\bcipher\s+/w|vssadmin\s+delete|\bbcdedit\b|"
    r"git\s+push\b[^|;&]*(\s--force|\s-f\b|\s--mirror|\s--delete|\s-d\b|\s\+\S)|git\s+reset\s+--hard|"
    r"git\s+clean\s+-[a-z]*f|git\s+branch\s+-d\b|git\s+checkout\s+(--\s+)?\.(\s|$)|git\s+filter-(branch|repo)\b|"
    r"\bdrop\s+(table|database|schema)\b|\btruncate\s+table\b|\bdelete\s+from\b|"
    r"\bmkfs\b|\bdd\s+[^|;&]*\bof=|>\s*/dev/(sd|nvme|hd|mmcblk|disk)|\bchmod\s+(-[a-z]*r|--recursive)|"
    r"\bchown\s+(-[a-z]*r|--recursive)|\bmv\s+[^|;&]*\s/dev/null\b|"
    r"\bsudo\b|\bsu\s+-|\bpkexec\b|\bdoas\b|\brunas\b|"
    r"\b(shutdown|reboot|poweroff|halt)\b|\binit\s+[06]\b|systemctl\s+(stop|disable|mask|poweroff|reboot|halt)\b|"
    r"stop-computer|restart-computer|reg\s+delete|remove-itemproperty|set-executionpolicy|"
    r":\(\)\s*\{|"
    r"(curl|wget|iwr|irm|invoke-webrequest|invoke-restmethod)\b[^;&]*\|\s*(sudo\s+)?(ba|z|da|k|fi)?sh\b|"
    r"\|\s*(sudo\s+)?(ba|z|da)?sh\s*$|\|\s*(python3?|node|perl|ruby|pwsh|powershell)(\s+-)?\s*$|"
    r"\b(ba|z|da)?sh\s+<\(|\beval\b|\biex\b|invoke-expression|base64\s+(-d|--decode)|frombase64string|"
    r"-encodedcommand\b|\$\((curl|wget)|`(curl|wget))",
    re.I,
)


def _normalise(command: str) -> str:
    """Undo common hiding tricks so the patterns see the command the shell will run."""
    c = command.replace("\x00", " ")
    c = re.sub(r"\$\{?IFS\}?", " ", c)          # rm${IFS}-rf
    c = re.sub(r"\$'([^']*)'", r"\1", c)         # $'rm'
    c = re.sub(r"[\"'\\^]", "", c)               # r''m, r\m, "rm", cmd's r^m
    c = re.sub(r"\s+", " ", c)                   # newlines, tabs, runs of spaces
    return c.strip()


def is_destructive(command: str) -> bool:
    if not command:
        return False
    return bool(DESTRUCTIVE.search(command) or DESTRUCTIVE.search(_normalise(command)))


# The daemon's own keys (model APIs, bots, ...) never reach commands it runs. A command that needs one gets it from
# its project's own config, or through Orion's key proxy (key_proxy.py). ORION_RUNNER_KEEP_ENV=NAME,NAME keeps some.
SECRET_ENV = re.compile(r"(^|_)(API_?KEY|KEY|TOKEN|SECRET|PASSWORD|PASSWD|PASS|API_HASH|PRIVATE_KEY|CREDENTIALS?)($|_)",
                        re.I)


def child_env(extra: Optional[dict[str, str]] = None) -> dict[str, str]:
    keep = {k.strip() for k in os.environ.get("ORION_RUNNER_KEEP_ENV", "").split(",") if k.strip()}
    env = {k: v for k, v in os.environ.items() if k in keep or not SECRET_ENV.search(k)}
    env.pop("ORION_TOKEN_FILE", None)
    return {**env, **(extra or {})}


def shell_argv(command: str) -> list[str]:
    if sys.platform == "win32":
        return ["cmd.exe", "/d", "/s", "/c", command]
    return ["/bin/bash", "-lc", command]


def _no_window() -> dict[str, Any]:
    if sys.platform == "win32":
        return {"creationflags": 0x08000000}  # CREATE_NO_WINDOW
    return {}


# ------------------------------------------------------------------ diagnosis
PORT_IN_USE = re.compile(r"EADDRINUSE|address already in use|port \d{2,5} is (already )?in use|"
                         r"Only one usage of each socket address", re.I)
PORT_NUMBER = re.compile(r"(?:port\s+|:)(\d{2,5})\b", re.I)


def _port_fix(m: re.Match[str]) -> tuple[str, Optional[str]]:
    line_start = m.string.rfind("\n", 0, m.start()) + 1
    line_end = m.string.find("\n", m.end())
    line = m.string[line_start: line_end if line_end != -1 else None]
    port = PORT_NUMBER.search(line)
    return ("Another process is already using the port.",
            f"npx --yes kill-port {port.group(1)}" if port else None)


QUICK_FIXES: list[tuple[re.Pattern[str], Callable[[re.Match[str]], tuple[str, Optional[str]]]]] = [
    (PORT_IN_USE, _port_fix),
    (re.compile(r"ModuleNotFoundError: No module named '([\w.\-]+)'"),
     lambda m: (f"Python package '{m.group(1)}' is missing.", f"python -m pip install {m.group(1).split('.')[0]}")),
    (re.compile(r"Cannot find module '(@?[\w.\-]+(?:/[\w.\-]+)?)'"),
     lambda m: (f"Node package '{m.group(1)}' is missing.", f"npm install {m.group(1)}")),
    (re.compile(r"(npm ERR! missing script: (\S+))", re.I),
     lambda m: (f"There is no npm script called '{m.group(2)}' in this project.", None)),
    (re.compile(r"'(\S+)' is not recognized as an internal or external command|(\S+): command not found"),
     lambda m: (f"The command '{m.group(1) or m.group(2)}' isn't installed or isn't on PATH.", None)),
    (re.compile(r"node_modules.*(not found|missing)|Cannot find module.*node_modules", re.I),
     lambda m: ("Dependencies aren't installed.", "npm install")),
]

DIAGNOSE_SYSTEM = """You diagnose failed developer commands on the user's Windows PC.
Return JSON: {"diagnosis": "one or two plain sentences", "fix_command": "a single shell command that fixes the
root cause, or empty string if a human must act"}. Never suggest destructive commands (deleting files,
force-pushing, resetting repos). Prefer the smallest fix."""


async def diagnose(command: str, tail: str, orchestrator: Any = None) -> dict[str, Any]:
    for pattern, fn in QUICK_FIXES:
        m = pattern.search(tail)
        if m:
            diagnosis, fix = fn(m)
            if fix and is_destructive(fix):
                fix = None
            return {"diagnosis": diagnosis, "fix_command": fix or "", "source": "heuristic"}
    if orchestrator is not None:
        try:
            raw, model = await orchestrator.frontier_text(
                DIAGNOSE_SYSTEM, f"Command: {command}\n\nLast output:\n{tail[-4000:]}",
                json_mode=True, max_tokens=300, temperature=0.1, timeout=45.0)
            start, end = raw.find("{"), raw.rfind("}")
            data = json.loads(raw[start:end + 1]) if start >= 0 else {}
            fix = str(data.get("fix_command") or "").strip()
            if is_destructive(fix):
                fix = ""
            return {"diagnosis": str(data.get("diagnosis") or "The command failed.")[:500],
                    "fix_command": fix[:500], "source": model}
        except Exception as exc:
            log.debug("LLM diagnosis unavailable: %s", exc)
    return {"diagnosis": "The command failed — see the output above.", "fix_command": "", "source": "none"}


# ------------------------------------------------------------------ runs
@dataclass
class Run:
    id: str
    command: str
    cwd: Optional[str]
    label: str
    kind: str = "task"  # task | service
    started_at: float = field(default_factory=time.time)
    ended_at: Optional[float] = None
    exit_code: Optional[int] = None
    tail: collections.deque = field(default_factory=lambda: collections.deque(maxlen=TAIL_LINES))
    diagnosis: Optional[dict[str, Any]] = None
    proc: Any = None
    restarts: int = 0

    def info(self) -> dict[str, Any]:
        return {
            "id": self.id, "command": self.command, "cwd": self.cwd, "label": self.label, "kind": self.kind,
            "started_at": self.started_at, "ended_at": self.ended_at, "exit_code": self.exit_code,
            "running": self.ended_at is None, "diagnosis": self.diagnosis, "restarts": self.restarts,
            "tail": list(self.tail)[-60:],
        }


class ProcessRunner:
    def __init__(self, publish: Publish, orchestrator: Any = None) -> None:
        self.publish = publish
        self.orchestrator = orchestrator
        self.runs: dict[str, Run] = {}
        self.on_finish: list[Callable[[Run], Any]] = []   # e.g. crash_healer.observe_run
        # async fn(command, cwd) -> reason to refuse, or None. Package Reality Check (agent_safety.py) adds one.
        self.start_checks: list[Callable[[str, Optional[str]], Awaitable[Optional[str]]]] = []

    def list(self) -> list[dict[str, Any]]:
        runs = sorted(self.runs.values(), key=lambda r: r.started_at, reverse=True)[:40]
        return [r.info() for r in runs]

    async def start(self, command: str, *, cwd: Optional[str] = None, label: str = "", kind: str = "task",
                    timeout: Optional[float] = 900.0, run_id: Optional[str] = None,
                    env: Optional[dict[str, str]] = None) -> Run:
        run = Run(id=run_id or f"run-{uuid.uuid4().hex[:8]}", command=command, cwd=cwd,
                  label=label or command[:60], kind=kind)
        if cwd and not os.path.isdir(cwd):
            run.tail.append(f"[orion] working folder not found: {cwd}")
            cwd = None
        self.runs[run.id] = run
        self._trim()
        if await self._refused(run, cwd):
            return run
        await self.publish({"event": "RUN_STARTED", "run": run.info()})
        asyncio.create_task(self._execute(run, cwd, timeout if kind == "task" else None, env))
        return run

    async def _refused(self, run: Run, cwd: Optional[str]) -> bool:
        """Run the start checks; a refused run ends at once with the reason in its output (exit code 126)."""
        for check in self.start_checks:
            try:
                why = await check(run.command, cwd)
            except Exception:
                log.exception("run start check failed")
                why = None
            if why:
                run.tail.extend(f"[orion] {line}" for line in str(why).splitlines()[:40])
                run.exit_code, run.ended_at = 126, time.time()
                await self.publish({"event": "RUN_STARTED", "run": run.info()})
                await self.publish({"event": "RUN_FINISHED", "run": run.info()})
                return True
        return False

    def _trim(self) -> None:
        if len(self.runs) > 80:
            done = sorted((r for r in self.runs.values() if r.ended_at), key=lambda r: r.ended_at or 0)
            for r in done[: len(self.runs) - 80]:
                self.runs.pop(r.id, None)

    async def _execute(self, run: Run, cwd: Optional[str], timeout: Optional[float],
                       env: Optional[dict[str, str]]) -> None:
        try:
            kwargs: dict[str, Any] = dict(
                cwd=cwd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
                stdin=asyncio.subprocess.DEVNULL, env=child_env({**(env or {}), "PYTHONUNBUFFERED": "1",
                                                                 "FORCE_COLOR": "0"}), **_no_window())
            if sys.platform == "win32":
                # cmd.exe can't parse list2cmdline's \" escaping; the shell form passes the line through verbatim.
                proc = await asyncio.create_subprocess_shell(run.command, **kwargs)
            else:
                proc = await asyncio.create_subprocess_exec(*shell_argv(run.command), **kwargs)
        except Exception as exc:
            run.tail.append(f"[orion] could not start: {exc}")
            await self._finish(run, -1)
            return
        run.proc = proc
        buffer: list[str] = []
        last_flush = time.monotonic()

        async def flush() -> None:
            nonlocal buffer, last_flush
            if buffer:
                await self.publish({"event": "RUN_OUTPUT", "run_id": run.id, "lines": buffer})
                buffer = []
            last_flush = time.monotonic()

        async def pump() -> None:
            assert proc.stdout is not None
            while True:
                line = await proc.stdout.readline()
                if not line:
                    break
                text = line.decode("utf-8", errors="replace").rstrip()
                text = re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", text)  # strip ANSI colours
                run.tail.append(text)
                buffer.append(text)
                if len(buffer) >= 30 or time.monotonic() - last_flush > 0.4:
                    await flush()
            await flush()

        try:
            if timeout:
                await asyncio.wait_for(pump(), timeout=timeout)
            else:
                await pump()
            code = await proc.wait()
        except asyncio.TimeoutError:
            run.tail.append(f"[orion] stopped after {int(timeout or 0)} s timeout")
            proc.kill()
            code = await proc.wait()
        await self._finish(run, code)

    async def _finish(self, run: Run, code: int) -> None:
        run.exit_code = code
        run.ended_at = time.time()
        if code != 0 and not getattr(run, "_stopped", False):
            run.diagnosis = await diagnose(run.command, "\n".join(run.tail), self.orchestrator)
        await self.publish({"event": "RUN_FINISHED", "run": run.info()})
        for hook in self.on_finish:
            try:
                hook(run)
            except Exception:
                log.exception("run finish hook failed")

    async def stop(self, run_id: str) -> bool:
        run = self.runs.get(run_id)
        if not run or not run.proc or run.ended_at:
            return False
        setattr(run, "_stopped", True)
        try:
            if sys.platform == "win32":
                # kill the whole tree (dev servers spawn children)
                await asyncio.create_subprocess_exec("taskkill", "/PID", str(run.proc.pid), "/T", "/F", **_no_window())
            else:
                run.proc.terminate()
        except ProcessLookupError:
            pass
        return True

    async def fix_and_restart(self, run_id: str) -> Optional[Run]:
        """Run the suggested fix, then the original command again (same label/kind)."""
        run = self.runs.get(run_id)
        if not run:
            return None
        fix = (run.diagnosis or {}).get("fix_command", "")
        if is_destructive(run.command) or is_destructive(fix):
            # one approval covers one run: destructive commands go back through the approval queue
            run.tail.append("[orion] This command is destructive — run it again from Scripts so you can approve it.")
            await self.publish({"event": "RUN_FINISHED", "run": run.info()})
            return None
        if fix:
            fix_run = await self.start(fix, cwd=run.cwd, label=f"Fix: {fix[:50]}")
            while fix_run.ended_at is None:
                await asyncio.sleep(0.3)
            if fix_run.exit_code != 0:
                return fix_run
        new = await self.start(run.command, cwd=run.cwd, label=run.label, kind=run.kind)
        new.restarts = run.restarts + 1
        return new
