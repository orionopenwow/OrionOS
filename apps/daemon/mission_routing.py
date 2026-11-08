"""Mission Control's model router (after-1.0 plan 8.1): route work to the coding agent that is best at it.

The rules live in a versioned routing.json (next to this file; a copy at ~/.config/orion/routing.json or
ORION_ROUTING_FILE wins): task type -> agent, fallback agents, optional model and a cost cap per mission.
Missions use it when you say "let Orion pick" (agent=None), and to choose the second agent that reviews the work.
A broken override file is ignored (logged), never fatal.
"""
from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import Any, Iterable, Optional

log = logging.getLogger("orion.routing")

BUILTIN = Path(__file__).resolve().parent / "routing.json"
FALLBACK_RULES: dict[str, Any] = {
    "version": 0,
    "default": {"agent": "claude", "fallback": ["codex", "gemini"], "model": None, "cost_cap_usd": 2.0,
                "time_limit_min": 30},
    "review": {"agents": ["codex", "claude", "gemini"], "model": None, "cost_cap_usd": 0.5, "time_limit_min": 10},
    "tasks": {},
}


def routing_path() -> Path:
    env = os.environ.get("ORION_ROUTING_FILE", "").strip()
    if env:
        return Path(env).expanduser()
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    user = Path(base) / "orion" / "routing.json"
    return user if user.is_file() else BUILTIN


def _valid(raw: Any) -> bool:
    return (isinstance(raw, dict) and isinstance(raw.get("version"), int) and isinstance(raw.get("tasks", {}), dict)
            and isinstance(raw.get("default", {}), dict))


def load_routing(path: Optional[Path] = None) -> dict[str, Any]:
    """The routing rules. Falls back to the built-in file, then to a minimal table."""
    for cand in ([path] if path else []) + [routing_path(), BUILTIN]:
        try:
            raw = json.loads(Path(cand).read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            if cand != BUILTIN:
                log.warning("routing file %s unreadable: %s", cand, exc)
            continue
        if _valid(raw):
            rules = {**FALLBACK_RULES, **raw}
            rules["source"] = str(cand)
            return rules
        log.warning("routing file %s has no integer 'version' or a bad shape; ignored", cand)
    return {**FALLBACK_RULES, "source": "built-in defaults"}


def classify(text: str, rules: dict[str, Any], template_task: Optional[str] = None) -> str:
    """Task type for a mission: the template's, else the best keyword match, else 'default'."""
    tasks = rules.get("tasks") or {}
    if template_task and template_task in tasks:
        return template_task
    low = f" {(text or '').lower()} "
    best, score = "default", 0
    for name, spec in tasks.items():
        hits = sum(1 for k in spec.get("keywords") or [] if re.search(rf"\b{re.escape(str(k).lower())}\b", low))
        if hits > score:
            best, score = name, hits
    return best


def _spec(rules: dict[str, Any], task: str) -> dict[str, Any]:
    return {**(rules.get("default") or {}), **((rules.get("tasks") or {}).get(task) or {})}


def pick(task: str, installed: Iterable[str], rules: dict[str, Any]) -> dict[str, Any]:
    """{"agent", "model", "cost_cap_usd", "time_limit_min", "task", "why"}; agent None if nothing usable is installed."""
    have = set(installed)
    spec = _spec(rules, task)
    order = [spec.get("agent")] + list(spec.get("fallback") or []) + list((rules.get("default") or {}).get("fallback") or [])
    seen: list[str] = []
    for a in order:
        if a and a not in seen:
            seen.append(a)
    chosen = next((a for a in seen if a in have), None)
    first = seen[0] if seen else None
    if chosen is None:
        why = "No coding agent Orion can drive is installed (Claude Code, Codex or Gemini CLI)."
    elif chosen == first:
        why = f"{spec.get('role') or 'Best fit'} for {task} work, per routing.json."
    else:
        why = f"{first} would be first for {task} work but isn't installed, so the fallback {chosen} was picked."
    return {"agent": chosen, "model": spec.get("model") if chosen == first else None,
            "cost_cap_usd": float(spec.get("cost_cap_usd") or 2.0),
            "time_limit_min": float(spec.get("time_limit_min") or 30), "task": task, "why": why}


def reviewer(implementer: str, installed: Iterable[str], rules: dict[str, Any]) -> Optional[str]:
    """A second agent to check the work: a different one when possible, else the same tool, else None."""
    have = [a for a in (rules.get("review") or {}).get("agents") or [] if a in set(installed)]
    other = next((a for a in have if a != implementer), None)
    if other:
        return other
    return implementer if implementer in have else None
