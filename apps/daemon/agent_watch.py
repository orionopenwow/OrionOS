"""Coding-agent watcher for the pet: notices when an AI coding agent is working on this PC.

Sources (each is cheap: only file modification times plus the tail of the newest log are read):

  Claude Code   ~/.claude/projects/<project>/<session>.jsonl   (tool names + target file)
  Codex         ~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl     (tool names + command)
  Antigravity   ~/.gemini/antigravity/{conversations,brain,code_tracker}
  Gemini CLI    ~/.gemini/tmp/**
  Cursor        ~/.cursor/projects/**, ~/.cursor/ai-tracking/**

Plus two signals for agents that leave no local logs (e.g. Claude in the desktop app, which runs in the cloud):
  * POST /agent/ping  {"agent": "Claude", "status": "Editing App.tsx", "project": "orion"}  (expires after 30 s)
  * a burst of file changes inside a known workspace -> "Something is editing <project>"

Only modification times, tool names and short targets are read — never file contents or your messages.
Extra folders can be added with ORION_AGENT_DIRS="Name=path;Name=path".
"""
from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

ACTIVE_S = 12.0        # a log touched this recently -> working
DONE_HOLD_S = 25.0     # show "done" this long after work stops
PING_TTL_S = 30.0
TAIL_BYTES = 96_000
MAX_ENTRIES = 6000     # cap per source scan so a huge folder can't stall the daemon
BURST_FILES = 3        # this many workspace files changed within BURST_WINDOW_S counts as "something is editing"
BURST_WINDOW_S = 20.0
SKIP_DIRS = {"node_modules", ".git", "target", "dist", "build", ".venv", "venv", "__pycache__", ".next", ".cache",
             "logs", ".pytest_cache", "resources"}


# ------------------------------------------------------------------ helpers
def _newest(root: Path, suffixes: Optional[tuple[str, ...]] = None, max_depth: int = 6) -> Optional[tuple[float, Path]]:
    """Newest file under root (bounded walk)."""
    best: Optional[tuple[float, Path]] = None
    seen = 0
    stack: list[tuple[Path, int]] = [(root, 0)]
    while stack and seen < MAX_ENTRIES:
        d, depth = stack.pop()
        try:
            with os.scandir(d) as it:
                for e in it:
                    seen += 1
                    try:
                        if e.is_dir(follow_symlinks=False):
                            if depth < max_depth and e.name not in SKIP_DIRS:
                                stack.append((Path(e.path), depth + 1))
                        elif suffixes is None or e.name.endswith(suffixes):
                            m = e.stat().st_mtime
                            if best is None or m > best[0]:
                                best = (m, Path(e.path))
                    except OSError:
                        continue
        except OSError:
            continue
    return best


def _tail_lines(path: Path) -> list[str]:
    try:
        with path.open("rb") as fh:
            fh.seek(max(0, path.stat().st_size - TAIL_BYTES))
            return fh.read().decode("utf-8", errors="replace").splitlines()
    except OSError:
        return []


def describe_tool(tool: str, args: dict[str, Any]) -> str:
    name = tool.lower()
    target = args.get("file_path") or args.get("path") or args.get("notebook_path") or args.get("file") or ""
    short = Path(str(target)).name if target else ""
    if name in ("edit", "multiedit", "write", "notebookedit", "str_replace_editor", "apply_patch", "write_file",
                "replace", "edit_file"):
        return f"Editing {short}" if short else "Editing code"
    if name in ("read", "view", "read_file"):
        return f"Reading {short}" if short else "Reading code"
    if name in ("grep", "glob", "ls", "search", "list_dir", "codebase_search", "grep_search", "file_search"):
        return "Searching the codebase"
    if name in ("bash", "shell", "exec_command", "run_terminal_cmd", "run_shell_command", "local_shell"):
        desc = str(args.get("description") or "").strip()
        cmd = args.get("command") or args.get("cmd") or ""
        if isinstance(cmd, list):
            cmd = " ".join(str(c) for c in cmd)
        cmd = str(cmd).strip().split("\n")[0]
        if desc:
            return desc[:60]
        if "test" in cmd:
            return "Running tests"
        if any(w in cmd for w in ("build", "tsc", "cargo", "make")):
            return "Building"
        if "git " in cmd:
            return "Working with git"
        return "Running a command"
    if name in ("webfetch", "websearch", "web_search", "fetch"):
        return "Researching online"
    if name in ("task", "agent", "spawn_agent"):
        return "Delegating to a helper"
    if name in ("todowrite", "taskcreate", "taskupdate", "update_plan"):
        return "Planning the next steps"
    return f"Using {tool}"


# ------------------------------------------------------------------ parsers for the newest log
def parse_claude(path: Path) -> str:
    for line in reversed(_tail_lines(path)):
        try:
            row = json.loads(line)
        except ValueError:
            continue
        msg = row.get("message") if isinstance(row, dict) else None
        if not isinstance(msg, dict) or row.get("type") != "assistant":
            continue
        content = msg.get("content")
        if isinstance(content, list):
            for block in reversed(content):
                if isinstance(block, dict) and block.get("type") == "tool_use":
                    return describe_tool(str(block.get("name", "")), block.get("input") or {})
                if isinstance(block, dict) and block.get("type") == "text" and block.get("text"):
                    return "Thinking it through"
    return "Thinking it through"


def parse_codex(path: Path) -> str:
    for line in reversed(_tail_lines(path)):
        try:
            row = json.loads(line)
        except ValueError:
            continue
        item = row.get("payload", row) if isinstance(row, dict) else {}
        if not isinstance(item, dict):
            continue
        if item.get("type") in ("function_call", "local_shell_call", "custom_tool_call"):
            args: Any = item.get("arguments") or item.get("input") or item.get("action") or {}
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except ValueError:
                    args = {"command": args}
            return describe_tool(str(item.get("name") or "shell"), args if isinstance(args, dict) else {})
        if item.get("type") in ("reasoning", "message") and item.get("role", "assistant") == "assistant":
            return "Thinking it through"
    return "Working"


def _project_from_claude(path: Path) -> str:
    parts = [p for p in path.parent.name.replace("_", "-").split("-") if p]
    return "-".join(parts[-2:]) if len(parts) >= 2 else (path.parent.name or "project")


@dataclass
class Source:
    agent: str
    root: Path
    suffixes: Optional[tuple[str, ...]] = None
    parser: Optional[Callable[[Path], str]] = None
    project_fn: Optional[Callable[[Path], str]] = None
    every_s: float = 2.5
    _next: float = 0.0
    _last: Optional[tuple[float, Path]] = field(default=None, repr=False)

    def newest(self, now: float) -> Optional[tuple[float, Path]]:
        if now >= self._next:
            self._next = now + self.every_s
            self._last = _newest(self.root, self.suffixes) if self.root.is_dir() else None
        return self._last


def default_sources(home: Optional[Path] = None) -> list[Source]:
    home = home or Path.home()
    sources = [
        Source("Claude", home / ".claude" / "projects", (".jsonl",), parse_claude, _project_from_claude),
        Source("Codex", home / ".codex" / "sessions", (".jsonl",), parse_codex, None, every_s=3.0),
        Source("Antigravity", home / ".gemini" / "antigravity" / "conversations", None, None, None, every_s=4.0),
        Source("Antigravity", home / ".gemini" / "antigravity" / "brain", None, None, None, every_s=5.0),
        Source("Antigravity", home / ".gemini" / "antigravity" / "code_tracker", None, None, None, every_s=5.0),
        Source("Gemini", home / ".gemini" / "tmp", None, None, None, every_s=4.0),
        Source("Cursor", home / ".cursor" / "ai-tracking", None, None, None, every_s=4.0),
        Source("Cursor", home / ".cursor" / "projects", None, None, lambda p: p.relative_to(home / ".cursor" / "projects").parts[0], every_s=5.0),
    ]
    for spec in os.environ.get("ORION_AGENT_DIRS", "").split(";"):
        if "=" in spec:
            name, path = spec.split("=", 1)
            sources.append(Source(name.strip() or "Agent", Path(path.strip()).expanduser(), None, None, None, every_s=4.0))
    return sources


# ================================================================== swarm: one entry per running agent
# The HUD draws each entry as a small robot orbiting the core. Same privacy rule as above: only tool
# names, short targets (file names, the agent's own one-line command descriptions), timestamps and the
# working folder are kept. File contents, tool output and your messages are never read or kept.

SWARM_RECENT_S = 30 * 60   # session logs touched this recently are candidates
SWARM_DONE_S = 90.0        # a finished agent stays on screen this long
SWARM_WAIT_S = 45.0        # a shell command quiet this long might be waiting for your OK
SWARM_STUCK_S = 10 * 60    # no progress this long -> stuck
SWARM_MAX = 8
SWARM_EVENTS = 12
SWARM_FILES = 12
SHELL_TOOLS = {"bash", "shell", "exec_command", "run_terminal_cmd", "run_shell_command", "local_shell"}
DELEGATE_TOOLS = {"task", "agent", "spawn_agent"}
EDIT_TOOLS = {"edit", "multiedit", "write", "notebookedit", "str_replace_editor", "write_file", "replace", "edit_file",
              "create_file"}
PATCH_FILE = re.compile(r"\*\*\* (?:Update|Add|Delete) File: ([^\n\r]+)")


def _recent_files(root: Path, suffixes: Optional[tuple[str, ...]], since: float, limit: int = SWARM_MAX,
                  max_depth: int = 6) -> list[tuple[float, Path]]:
    """Files under root modified after `since`, newest first (bounded walk)."""
    found: list[tuple[float, Path]] = []
    seen = 0
    stack: list[tuple[Path, int]] = [(root, 0)]
    while stack and seen < MAX_ENTRIES:
        d, depth = stack.pop()
        try:
            with os.scandir(d) as it:
                for e in it:
                    seen += 1
                    try:
                        if e.is_dir(follow_symlinks=False):
                            if depth < max_depth and e.name not in SKIP_DIRS:
                                stack.append((Path(e.path), depth + 1))
                        elif suffixes is None or e.name.endswith(suffixes):
                            m = e.stat().st_mtime
                            if m >= since:
                                found.append((m, Path(e.path)))
                    except OSError:
                        continue
        except OSError:
            continue
    found.sort(key=lambda t: t[0], reverse=True)
    return found[:limit]


def _ts(value: Any) -> Optional[float]:
    if isinstance(value, (int, float)):
        return float(value) / (1000.0 if value > 1e11 else 1.0)
    if isinstance(value, str) and value:
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
        except ValueError:
            return None
    return None


def _file_names(tool: str, args: dict[str, Any]) -> list[str]:
    """Names (never paths or contents) of files a tool call writes."""
    name = tool.lower()
    if name == "apply_patch":
        patch = args.get("input") or args.get("patch") or args.get("command") or ""
        if isinstance(patch, list):
            patch = "\n".join(str(p) for p in patch)
        return [Path(m.strip()).name for m in PATCH_FILE.findall(str(patch))][:SWARM_FILES]
    if name in EDIT_TOOLS:
        target = args.get("file_path") or args.get("path") or args.get("notebook_path") or args.get("file") or ""
        return [Path(str(target)).name] if target else []
    return []


@dataclass
class SessionInfo:
    """What a session log says, reduced to safe facts."""
    status: str = "Working"
    events: list[dict[str, Any]] = field(default_factory=list)   # [{"at": epoch|None, "text": str}]
    files: list[str] = field(default_factory=list)
    cwd: str = ""
    started: Optional[float] = None
    hint: str = ""          # "" | "pending" | "pending_shell" | "error" | "ended"


def _push_files(info: SessionInfo, names: list[str]) -> None:
    for n in names:
        if n in info.files:
            info.files.remove(n)
        info.files.append(n)
    del info.files[:-SWARM_FILES]


def parse_claude_session(path: Path) -> SessionInfo:
    info = SessionInfo(status="Thinking it through")
    pending: dict[str, str] = {}   # tool_use id -> tool name, until its result arrives
    last_kind = ""
    for line in _tail_lines(path):
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if not isinstance(row, dict) or row.get("type") not in ("assistant", "user"):
            continue
        if row.get("isApiErrorMessage") and _limit_error(row.get("message")):   # Claude's own "usage limit" notice
            last_kind = "limit"
            continue
        at = _ts(row.get("timestamp"))
        if at and (info.started is None or at < info.started):
            info.started = at
        if isinstance(row.get("cwd"), str):
            info.cwd = row["cwd"]
        msg = row.get("message")
        content = msg.get("content") if isinstance(msg, dict) else None
        if row["type"] == "assistant" and isinstance(content, list):
            for block in content:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "tool_use":
                    tool = str(block.get("name", ""))
                    args = block.get("input") if isinstance(block.get("input"), dict) else {}
                    text = describe_tool(tool, args)
                    info.events.append({"at": at, "text": text})
                    info.status = text
                    _push_files(info, _file_names(tool, args))
                    pending[str(block.get("id", ""))] = tool.lower()
                    last_kind = "tool"
                elif block.get("type") == "text":
                    last_kind = "text"
            if isinstance(msg, dict) and msg.get("stop_reason") == "end_turn" and last_kind != "tool":
                last_kind = "ended"
        elif row["type"] == "user" and isinstance(content, list):
            # only the tool_result envelope is looked at (its id and error flag), never what it says
            kinds = [b for b in content if isinstance(b, dict) and b.get("type") == "tool_result"]
            for block in kinds:
                pending.pop(str(block.get("tool_use_id", "")), None)
                last_kind = "error" if block.get("is_error") else "result"
            if not kinds:
                last_kind = "asked"
        elif row["type"] == "user":
            last_kind = "asked"   # you sent a message; the agent is about to work
    if pending and last_kind in ("tool", "result", "error") and set(pending.values()) <= DELEGATE_TOOLS:
        info.hint = "pending_helpers"   # its helper agents are doing the work
        info.status = "Waiting on its helpers"
    elif pending and last_kind == "tool":
        info.hint = "pending_shell" if set(pending.values()) <= SHELL_TOOLS | DELEGATE_TOOLS else "pending"
    elif last_kind == "error":
        info.hint = "error"
    elif last_kind == "limit":
        info.hint = "limit"
    elif last_kind == "ended":
        info.hint = "ended"
        info.status = "Finished"
    del info.events[:-SWARM_EVENTS]
    return info


LIMIT_WORDS = re.compile(r"usage limit|rate limit|limit reached|quota|out of (credits|usage)|hit your .*limit", re.I)


def _limit_error(msg: Any) -> bool:
    """Only the agent tool's own error notice is looked at (never your messages or file contents)."""
    if isinstance(msg, dict):
        msg = msg.get("content") if msg.get("content") is not None else msg.get("message", "")
    if isinstance(msg, list):
        msg = " ".join(str(b.get("text", "")) for b in msg if isinstance(b, dict))
    return bool(LIMIT_WORDS.search(str(msg or "")[:500]))


def parse_codex_session(path: Path) -> SessionInfo:
    info = SessionInfo(status="Working")
    try:   # the first line is session metadata; only its working folder and start time are kept
        with path.open("rb") as fh:
            head = json.loads(fh.readline(256_000).decode("utf-8", errors="replace") or "{}")
        if isinstance(head, dict):
            meta = head.get("payload", head)
            if isinstance(meta, dict) and isinstance(meta.get("cwd"), str):
                info.cwd = meta["cwd"]
            info.started = _ts(head.get("timestamp"))
    except (OSError, ValueError):
        pass
    open_calls: set[str] = set()
    last_kind = ""
    for line in _tail_lines(path):
        try:
            row = json.loads(line)
        except ValueError:
            continue
        item = row.get("payload", row) if isinstance(row, dict) else {}
        if not isinstance(item, dict):
            continue
        at = _ts(row.get("timestamp"))
        kind = item.get("type")
        if kind in ("function_call", "local_shell_call", "custom_tool_call"):
            args: Any = item.get("arguments") or item.get("input") or item.get("action") or {}
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except ValueError:
                    args = {"input": args} if kind == "custom_tool_call" else {"command": args}
            args = args if isinstance(args, dict) else {}
            tool = str(item.get("name") or "shell")
            text = describe_tool(tool, args)
            info.events.append({"at": at, "text": text})
            info.status = text
            _push_files(info, _file_names(tool, args))
            open_calls.add(str(item.get("call_id", "")))
            last_kind = "tool"
        elif kind in ("function_call_output", "local_shell_call_output", "custom_tool_call_output"):
            open_calls.discard(str(item.get("call_id", "")))
            last_kind = "result"
        elif kind == "message" and item.get("role") == "assistant":
            last_kind = "ended"
        elif kind == "message" and item.get("role") == "user":
            last_kind = "asked"
        elif kind == "error" and _limit_error(item.get("message")):   # Codex's own "usage limit" notice
            last_kind = "limit"
    if last_kind == "limit":
        info.hint = "limit"
    elif last_kind == "tool" and open_calls:
        info.hint = "pending_shell"
    elif last_kind == "ended":
        info.hint = "ended"
        info.status = "Finished"
    del info.events[:-SWARM_EVENTS]
    return info


SESSION_PARSERS: dict[str, Callable[[Path], SessionInfo]] = {"Claude": parse_claude_session,
                                                              "Codex": parse_codex_session}


def _mins(s: float) -> str:
    m = int(s // 60)
    return f"{m} min" if m < 60 else f"{m // 60} h {m % 60} min"


def judge(info: SessionInfo, idle_s: float) -> Optional[tuple[str, str, str]]:
    """(state, status, reason) for a session idle this long, or None when it should leave the swarm."""
    if info.hint == "ended":
        return ("done", "Finished", "") if idle_s < SWARM_DONE_S else None
    if info.hint == "limit":   # Baton offers to hand its work to another agent
        return ("stuck", "Hit its usage limit", "Out of usage for now") if idle_s < SWARM_RECENT_S else None
    if idle_s < ACTIVE_S:
        return "working", info.status, ""
    if info.hint == "pending_helpers":
        return ("working", info.status, "") if idle_s < SWARM_RECENT_S else None
    if idle_s >= SWARM_STUCK_S:
        if idle_s >= SWARM_RECENT_S:
            return None
        return "stuck", "Stuck", f"No progress for {_mins(idle_s)}"
    if info.hint == "error":
        return "failed", "Hit an error", "The last step failed and it has gone quiet"
    if info.hint == "pending":
        return "waiting", "Waiting for your OK", "It wants to: " + info.status
    if info.hint == "pending_shell":
        if idle_s < SWARM_WAIT_S:
            return "working", info.status, ""
        return "waiting", "Waiting for your OK", "Or still busy with: " + info.status
    if idle_s < 120:
        return "working", info.status, ""
    return "stuck", "Gone quiet", f"No progress for {_mins(idle_s)}"


def _project_name(cwd: str, fallback: str) -> str:
    if cwd:
        name = Path(cwd.replace("\\", "/")).name
        if name:
            return name
    return fallback


def _same_dir(a: str, b: str) -> bool:
    if not a or not b:
        return False

    def norm(p: str) -> str:
        return os.path.normcase(os.path.normpath(p.replace("\\", "/")))
    return norm(a) == norm(b)


def _robot(aid: str, tool: str, project: str, **kw: Any) -> dict[str, Any]:
    base = {"id": aid, "tool": tool, "project": project, "name": tool + (f" · {project}" if project else ""),
            "cwd": "", "state": "working", "status": "Working", "reason": "", "last_activity": 0.0,
            "started": 0.0, "events": [], "files": [], "controllable": False, "run_id": None, "paused": False}
    base.update(kw)
    return base


class SwarmMixin:
    """Adds swarm() to AgentWatcher: every agent seen recently, shaped for the robots in the HUD."""

    sources: list[Source]
    _pings: dict[str, dict[str, Any]]

    def _swarm_init(self) -> None:
        self._swarm_cache: dict[str, tuple[float, SessionInfo]] = {}
        self._first_seen: dict[str, float] = {}
        self._ping_done: dict[str, tuple[float, dict[str, Any]]] = {}
        self._swarm_next: dict[int, float] = {}
        self._swarm_hits: dict[int, list[tuple[float, Path]]] = {}

    def _session(self, agent: str, path: Path, mtime: float) -> SessionInfo:
        key = str(path)
        hit = self._swarm_cache.get(key)
        if hit and hit[0] == mtime:
            return hit[1]
        parser = SESSION_PARSERS.get(agent)
        info = parser(path) if parser else SessionInfo()
        self._swarm_cache[key] = (mtime, info)
        if len(self._swarm_cache) > 64:
            for k in list(self._swarm_cache)[:-32]:
                self._swarm_cache.pop(k, None)
        return info

    def swarm(self, now: Optional[float] = None,
              launched: Optional[Iterable[dict[str, Any]]] = None) -> list[dict[str, Any]]:
        """launched: runs Orion started itself ({id, tool, cwd, label, running, paused, started_at, ended_at,
        exit_code}); only those can be paused or stopped."""
        now = now or time.time()
        out: list[dict[str, Any]] = []
        generic: dict[str, tuple[float, Source, Path]] = {}

        for idx, src in enumerate(self.sources):
            if not src.root.is_dir():
                continue
            if src.agent not in SESSION_PARSERS:
                hit = src.newest(now)
                if hit and now - hit[0] < SWARM_DONE_S + ACTIVE_S:
                    prev = generic.get(src.agent)
                    if not prev or hit[0] > prev[0]:
                        generic[src.agent] = (hit[0], src, hit[1])
                continue
            if now >= self._swarm_next.get(idx, 0.0):
                self._swarm_next[idx] = now + src.every_s
                self._swarm_hits[idx] = _recent_files(src.root, src.suffixes, now - SWARM_RECENT_S)
            for mtime, path in self._swarm_hits.get(idx, []):
                info = self._session(src.agent, path, mtime)
                verdict = judge(info, now - mtime)
                if not verdict:
                    continue
                state, status, reason = verdict
                fallback = ""
                if src.project_fn:
                    try:
                        fallback = src.project_fn(path)
                    except Exception:
                        fallback = ""
                project = _project_name(info.cwd, fallback)
                aid = f"{src.agent.lower()}:{path.stem}"
                first = self._first_seen.setdefault(aid, info.started or mtime)
                helper = " helper" if "subagents" in path.parts else ""
                out.append(_robot(
                    aid, src.agent, project, name=f"{src.agent}{helper}" + (f" · {project}" if project else ""),
                    cwd=info.cwd, state=state, status=status, reason=reason, last_activity=mtime,
                    started=min(first, info.started or first), events=list(info.events), files=list(info.files)))

        for agent, (mtime, src, path) in generic.items():
            project = ""
            if src.project_fn:
                try:
                    project = src.project_fn(path)
                except Exception:
                    project = ""
            working = now - mtime < ACTIVE_S
            aid = f"{agent.lower()}:{project or 'main'}"
            first = self._first_seen.setdefault(aid, mtime)
            out.append(_robot(aid, agent, project, state="working" if working else "done",
                              status="Working" if working else "Finished", last_activity=mtime, started=first))

        for key, p in list(self._pings.items()):
            if now - p["at"] > PING_TTL_S:
                continue
            aid = f"ping:{key.lower()}"
            state = p.get("state", "working")
            out.append(_robot(aid, key, p["project"], state=state if state in ("working", "waiting", "stuck", "failed")
                              else "working", status=p["status"], last_activity=p["at"],
                              started=self._first_seen.setdefault(aid, p["at"])))
        for key, (at, p) in list(self._ping_done.items()):
            if now - at > SWARM_DONE_S:
                self._ping_done.pop(key, None)
                continue
            out.append(_robot(f"ping:{key.lower()}", key, p.get("project", ""), state="done", status="Finished",
                              last_activity=at, started=self._first_seen.get(f"ping:{key.lower()}", at)))

        # Agents Orion started itself (through the process runner) can be paused and stopped.
        for run in launched or []:
            cwd = str(run.get("cwd") or "")
            if run.get("running"):
                match = next((a for a in out if a["tool"] == run["tool"] and _same_dir(a["cwd"], cwd)
                              and not a["controllable"]), None)
                if match is not None:
                    match.update(controllable=True, run_id=run["id"], paused=bool(run.get("paused")))
                    if run.get("paused"):
                        match.update(state="waiting", status="Paused", reason="You paused it")
                    continue
            ended = run.get("ended_at")
            if not run.get("running") and (not ended or now - ended > SWARM_DONE_S):
                continue
            failed = not run.get("running") and run.get("exit_code") not in (0, None)
            if run.get("running"):
                state = "waiting" if run.get("paused") else "working"
            else:
                state = "failed" if failed else "done"
            status = {"working": "Working", "waiting": "Paused", "failed": "Hit an error", "done": "Finished"}[state]
            reason = f"Exited with code {run.get('exit_code')}" if failed else (
                "You paused it" if run.get("paused") else "")
            out.append(_robot(
                f"run:{run['id']}", run["tool"], _project_name(cwd, str(run.get("label") or "")), cwd=cwd,
                state=state, status=status, reason=reason, last_activity=ended or now,
                started=run.get("started_at") or now,
                events=[{"at": run.get("started_at"), "text": "Started by Orion"}],
                controllable=bool(run.get("running")), run_id=run["id"], paused=bool(run.get("paused"))))

        rank = {"failed": 0, "stuck": 1, "waiting": 2, "working": 3, "done": 4}
        out.sort(key=lambda a: (rank.get(a["state"], 5), -a["last_activity"]))
        out = out[:SWARM_MAX]
        live = {a["id"] for a in out}
        for aid in list(self._first_seen):
            if aid not in live:
                self._first_seen.pop(aid, None)
        for a in out:
            a["can_open"] = bool(a["cwd"]) and Path(a["cwd"]).is_dir()
        return out


# ------------------------------------------------------------------ watcher
class AgentWatcher(SwarmMixin):
    def __init__(self, sources: Optional[list[Source]] = None,
                 workspaces: Optional[Callable[[], Iterable[dict[str, Any]]]] = None) -> None:
        self.sources = sources if sources is not None else default_sources()
        self.workspaces = workspaces
        self.state: dict[str, Any] = {"state": "idle", "status": "", "project": "", "since": time.time(), "agents": 0,
                                      "agent": "", "watching": []}
        self._working_since: Optional[float] = None
        self._last_active = 0.0
        self._pings: dict[str, dict[str, Any]] = {}
        self._ws_next = 0.0
        self._ws_hits: list[tuple[float, str]] = []
        self._ws_mtimes: dict[str, float] = {}
        self._swarm_init()

    # -- external check-ins (POST /agent/ping) ---------------------------------
    def ping(self, agent: str, status: str = "", project: str = "", state: str = "working") -> None:
        key = (agent or "Agent")[:40]
        if state in ("done", "idle", "stopped"):
            old = self._pings.pop(key, None)
            if old and state == "done":
                self._ping_done[key] = (time.time(), old)
            return
        self._pings[key] = {"agent": key, "status": (status or "Working")[:80], "project": (project or "")[:60],
                            "at": time.time(), "state": state}

    # -- workspace file bursts --------------------------------------------------
    def _workspace_burst(self, now: float) -> Optional[str]:
        if not self.workspaces or now < self._ws_next:
            hits = [p for t, p in self._ws_hits if now - t < BURST_WINDOW_S]
            return max(set(hits), key=hits.count) if len(hits) >= BURST_FILES else None
        self._ws_next = now + 6.0
        for ws in list(self.workspaces() or [])[:12]:
            root = Path(str(ws.get("path", "")))
            if not ws.get("exists") or not root.is_dir():
                continue
            newest = _newest(root, None, max_depth=5)
            if not newest:
                continue
            m, p = newest
            key = str(p)
            if now - m < BURST_WINDOW_S and self._ws_mtimes.get(key) != m:
                self._ws_mtimes[key] = m
                self._ws_hits.append((m, ws.get("name") or root.name))
        self._ws_hits = [(t, n) for t, n in self._ws_hits if now - t < BURST_WINDOW_S]
        hits = [n for _, n in self._ws_hits]
        return max(set(hits), key=hits.count) if len(hits) >= BURST_FILES else None

    def watching(self) -> list[str]:
        names = sorted({s.agent for s in self.sources if s.root.is_dir()})
        if self.workspaces:
            names.append("workspace file activity")
        return names

    def poll(self, now: Optional[float] = None) -> Optional[dict[str, Any]]:
        """Returns the new state when it changed, else None."""
        now = now or time.time()
        active: list[tuple[float, str, str, str]] = []   # (mtime, agent, status, project)

        for src in self.sources:
            hit = src.newest(now)
            if hit and now - hit[0] < ACTIVE_S:
                m, path = hit
                status = src.parser(path) if src.parser else "Working"
                project = ""
                if src.project_fn:
                    try:
                        project = src.project_fn(path)
                    except Exception:
                        project = ""
                active.append((m, src.agent, status, project))

        for key, p in list(self._pings.items()):
            if now - p["at"] > PING_TTL_S:
                self._pings.pop(key, None)
            else:
                active.append((p["at"], p["agent"], p["status"], p["project"]))

        probably = False
        if not active:
            burst = self._workspace_burst(now)
            if burst:
                active.append((now, "Something", f"Editing {burst}", burst))
                probably = True

        if active:
            active.sort(reverse=True)
            _, agent, status, project = active[0]
            if self._working_since is None:
                self._working_since = now
            self._last_active = now
            names = sorted({a for _, a, _, _ in active})
            label = status if agent in ("Something",) else f"{agent} · {status}" if len(names) == 1 else status
            nxt = {"state": "working", "status": label, "project": project, "since": self._working_since,
                   "agents": len(names), "agent": ", ".join(names), "probable": probably}
        elif self._working_since is not None and now - self._last_active < DONE_HOLD_S:
            nxt = {**self.state, "state": "done", "status": "Finished", "agents": 0}
            if self.state.get("state") == "working":
                nxt["duration"] = round(self._last_active - self._working_since)
        else:
            self._working_since = None
            nxt = {"state": "idle", "status": "", "project": self.state.get("project", ""), "since": now, "agents": 0,
                   "agent": ""}
        nxt["watching"] = self.watching()

        changed = (nxt["state"], nxt.get("status"), nxt.get("agents"), tuple(nxt["watching"])) != (
            self.state["state"], self.state.get("status"), self.state.get("agents"), tuple(self.state.get("watching", [])))
        self.state = nxt
        return nxt if changed else None
