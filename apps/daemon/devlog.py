"""Multi-Repo Git Radar & Automated Devlog Compiler (Feature Bible D3).

  * Radar: branch, uncommitted changes and the latest commits of every workspace with a .git folder.
  * Devlog: reads the commits (and diff stats) made across all repos since the last devlog and drafts
    your Discord community update + a changelog in your voice. The draft lands in the approval queue;
    approving posts it to comms_routing.json -> discord.devlog_channel_id (or just marks it copied).
    Runs on demand ("Orion, write the devlog") and automatically when a long coding session ends.
"""
from __future__ import annotations

import asyncio
import json
import logging
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Optional

log = logging.getLogger("orion.devlog")

_NO_WINDOW = {"creationflags": 0x08000000} if sys.platform == "win32" else {}


def git(path: str, *args: str, timeout: float = 8.0) -> str:
    try:
        out = subprocess.run(["git", "-C", path, *args], capture_output=True, text=True, timeout=timeout,
                             encoding="utf-8", errors="replace", **_NO_WINDOW)
        return out.stdout.strip() if out.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def repo_status(ws: dict[str, Any], commits: int = 5) -> Optional[dict[str, Any]]:
    path = ws["path"]
    if not (Path(path) / ".git").exists():
        return None
    log_lines = git(path, "log", f"-n{commits}", "--pretty=format:%h\x1f%s\x1f%ct\x1f%an")
    recent = []
    for line in log_lines.splitlines():
        parts = line.split("\x1f")
        if len(parts) == 4:
            recent.append({"hash": parts[0], "subject": parts[1][:140], "time": int(parts[2]), "author": parts[3]})
    dirty = [ln for ln in git(path, "status", "--porcelain").splitlines() if ln.strip()]
    return {
        "id": ws["id"], "name": ws["name"], "path": path,
        "branch": git(path, "rev-parse", "--abbrev-ref", "HEAD") or "?",
        "dirty_files": len(dirty),
        "recent": recent,
        "last_commit_at": recent[0]["time"] if recent else None,
    }


async def radar(workspaces: list[dict[str, Any]]) -> list[dict[str, Any]]:
    results = await asyncio.gather(*(asyncio.to_thread(repo_status, ws) for ws in workspaces if ws.get("exists")))
    repos = [r for r in results if r]
    repos.sort(key=lambda r: r["last_commit_at"] or 0, reverse=True)
    return repos


def commits_since(ws: dict[str, Any], since: float) -> list[dict[str, Any]]:
    path = ws["path"]
    if not (Path(path) / ".git").exists():
        return []
    out = git(path, "log", f"--since=@{int(since)}", "--no-merges", "--pretty=format:%h\x1f%s\x1f%b\x1e", "--stat=120")
    commits = []
    for block in out.split("\x1e"):
        block = block.strip()
        if not block:
            continue
        head, _, stat = block.partition("\n")
        parts = head.split("\x1f")
        if len(parts) >= 2:
            commits.append({"hash": parts[0], "subject": parts[1][:200], "body": (parts[2] if len(parts) > 2 else "")[:400],
                            "stat": stat.strip().splitlines()[-1][:120] if stat.strip() else ""})
    return commits[:60]


DEVLOG_SYSTEM = """You write the developer's community devlog in their own voice.
Return JSON: {"title": str, "discord_update": str, "changelog": str}
- discord_update: a casual, human Discord post (under 1500 characters, a few emoji are fine, no corporate fluff)
  grouping the work by project, highlighting what players/users will notice, ending with what's next if obvious.
- changelog: concise markdown bullets per project (Added / Changed / Fixed).
Only describe work that appears in the commits. Never invent features."""


class Devlog:
    def __init__(self, data_dir: Path, orchestrator: Any) -> None:
        self.state_path = data_dir / "devlog_state.json"
        self.orchestrator = orchestrator

    def last_at(self) -> float:
        try:
            return float(json.loads(self.state_path.read_text(encoding="utf-8")).get("last_at", 0))
        except (OSError, ValueError):
            return time.time() - 7 * 86400

    def mark(self) -> None:
        self.state_path.write_text(json.dumps({"last_at": time.time()}), encoding="utf-8")

    async def collect(self, workspaces: list[dict[str, Any]], since: Optional[float] = None) -> dict[str, list]:
        since = since or self.last_at()
        found = {}
        for ws in workspaces:
            if not ws.get("exists"):
                continue
            commits = await asyncio.to_thread(commits_since, ws, since)
            if commits:
                found[ws["name"]] = commits
        return found

    async def draft(self, workspaces: list[dict[str, Any]], rules: list[str],
                    since: Optional[float] = None) -> Optional[dict[str, Any]]:
        commits = await self.collect(workspaces, since)
        if not commits:
            return None
        lines = []
        for project, items in commits.items():
            lines.append(f"## {project}")
            for c in items:
                lines.append(f"- {c['subject']}" + (f" — {c['body'][:160]}" if c["body"] else "") +
                             (f" ({c['stat']})" if c["stat"] else ""))
        system = DEVLOG_SYSTEM + ("\n\nThe developer's rules:\n" + "\n".join(f"- {r}" for r in rules) if rules else "")
        text, model = await self.orchestrator.frontier_text(system, "\n".join(lines)[:12000], json_mode=True,
                                                             max_tokens=1500, temperature=0.5, timeout=120.0)
        start, end = text.find("{"), text.rfind("}")
        data = json.loads(text[start:end + 1]) if start >= 0 else {}
        return {
            "title": str(data.get("title") or "Devlog")[:120],
            "discord_update": str(data.get("discord_update") or "")[:1900],
            "changelog": str(data.get("changelog") or "")[:6000],
            "commit_count": sum(len(v) for v in commits.values()),
            "projects": list(commits.keys()),
            "model": model,
        }
