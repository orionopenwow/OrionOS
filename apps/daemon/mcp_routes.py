"""Daemon side of the MCP bus: runs and approvals for coding agents, shared with the HUD and your phone.

`orion-mcp` (mcp_server.py) is started by each coding agent. It calls these routes so that:
  * project commands run in the daemon's self-healing runner (visible in the HUD, they outlive the agent);
  * commits, database writes and unknown commands become boardroom decisions. Nothing runs until the
    decision is signed on the boardroom or a paired phone; then the executors below do the work.

Every route re-checks paths and commands with the same rules as the MCP server (mcp_tools.py): a local
process calling these routes directly gets no more than an agent would.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Optional

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

import boardroom
from boardroom import TaskType
from mcp_tools import (McpConfig, ToolError, _strip_sql, git, git_staged, grimoire_command,
                       grimoire_needs_approval, mysql_connection_settings, mysql_write, project_command_allowed,
                       redact, resolve_project)

log = logging.getLogger("orion.mcp.routes")

MCP_TASK_TYPES = (
    TaskType("mcp.git_commit", "Commit code for a coding agent", "code", reversible=True, money=False,
             sensitive=True),
    TaskType("mcp.db_write", "Change data in a staging database", "code", reversible=False, money=False,
             sensitive=True),
    TaskType("mcp.run", "Run a command for a coding agent", "code", reversible=False, money=False, sensitive=True),
)
for _t in MCP_TASK_TYPES:   # sensitive => autonomy is capped at "suggest + still ask": these always ask
    boardroom.TASK_TYPES.setdefault(_t.key, _t)

PROPOSER = "Coding agent (MCP)"
router = APIRouter(prefix="/mcp", tags=["mcp"])

_daemon: Any = None
_results: dict[str, dict[str, Any]] = {}   # decision id -> what happened after it was approved
_runs: list[str] = []                      # runner ids started through MCP
_jobs: set[asyncio.Task] = set()


def install(daemon: Any) -> None:
    """Hook into the running daemon: register executors for the decisions this module proposes."""
    global _daemon
    _daemon = daemon
    board = daemon.board
    for t in MCP_TASK_TYPES:
        board.store.x("INSERT OR IGNORE INTO autonomy(task_type) VALUES(?)", (t.key,))
    board.executors["mcp.git_commit"] = lambda d: _spawn(d, _exec_commit(d))
    board.executors["mcp.db_write"] = lambda d: _spawn(d, _exec_db_write(d))
    board.executors["mcp.run"] = lambda d: _spawn(d, _exec_run(d))


def _cfg() -> McpConfig:
    return McpConfig.load()   # re-read each time so edits to mcp.json apply without a restart


def _err(msg: str, status: int = 400, **extra: Any) -> JSONResponse:
    return JSONResponse({"error": msg, **extra}, status_code=status)


def _need_daemon() -> Optional[JSONResponse]:
    if _daemon is None:
        return _err("Orion is still starting.", 503)
    return None


# =====================================================================================  executors
def _spawn(d: dict[str, Any], coro: Any) -> dict[str, Any]:
    """Executors are called synchronously by the boardroom; the real work runs in the background."""
    if (frozen := __import__("freeze").blocked("run agent tools that change things")):  # kill switch (freeze.py)
        coro.close()
        raise ValueError(frozen)
    _results[d["id"]] = {"state": "running"}

    async def job() -> None:
        try:
            _results[d["id"]] = await coro
        except ToolError as exc:
            _results[d["id"]] = {"state": "failed", "error": str(exc)}
        except Exception as exc:
            log.exception("MCP decision %s failed", d["id"])
            _results[d["id"]] = {"state": "failed", "error": redact(f"{type(exc).__name__}: {exc}")[:500]}
    task = asyncio.get_running_loop().create_task(job())
    _jobs.add(task)
    task.add_done_callback(_jobs.discard)
    return {"started": True}


async def _exec_commit(d: dict[str, Any]) -> dict[str, Any]:
    det = d["packet"]["details"]
    project = resolve_project(det["project"], _cfg())
    now = await git_staged(project)
    if now["staged_hash"] != det["staged_hash"]:
        return {"state": "failed", "error": "The staged changes are different from what you approved, so nothing "
                                            "was committed. Ask the agent to propose the commit again."}
    code, out = await git(project, "commit", "-m", det["message"], timeout=300)
    if code != 0:
        return {"state": "failed", "error": redact(out.strip()[-1500:])}
    _, sha = await git(project, "rev-parse", "--short", "HEAD")
    return {"state": "done", "commit": sha.strip(), "output": redact(out.strip()[-800:])}


async def _exec_db_write(d: dict[str, Any]) -> dict[str, Any]:
    det = d["packet"]["details"]
    conn = mysql_connection_settings(_cfg(), det["connection"])   # host must still be on the staging list
    import db_time_machine   # Toolkit: DB Time Machine snapshots first (a failed snapshot stops the write)
    guard = await db_time_machine.before_write(conn["name"], det["sql"])
    res = await asyncio.to_thread(mysql_write, conn, det["sql"])
    return {"state": "done", **res, **(await db_time_machine.after_write(guard))}


async def _exec_run(d: dict[str, Any]) -> dict[str, Any]:
    det = d["packet"]["details"]
    project = resolve_project(det["project"], _cfg())
    run = await _daemon.ext.runner.start(det["command"], cwd=str(project), label=det.get("label") or "",
                                         kind="service" if det.get("long_running") else "task")
    _runs.append(run.id)
    return {"state": "started", "run_id": run.id}


# =====================================================================================  routes
@router.post("/propose")
async def propose(request: Request) -> Any:
    if (r := _need_daemon()) is not None:
        return r
    try:
        body = await request.json()
    except ValueError:
        return _err("Send JSON.")
    if not isinstance(body, dict):
        return _err("Send a JSON object.")
    cfg = _cfg()
    action = body.get("action")
    why = str(body.get("why") or "")[:200]
    try:
        if action == "git_commit":
            project = resolve_project(str(body.get("project") or ""), cfg)
            message = str(body.get("message") or "").strip()[:2000]
            if not message:
                return _err("A commit needs a message.")
            staged = await git_staged(project)
            if not staged["files"]:
                return _err("Nothing is staged to commit.")
            task, summary = "mcp.git_commit", (f"Commit {len(staged['files'])} file(s) on {staged['branch']} in "
                                               f"{project.name}: “{message.splitlines()[0][:70]}”")
            details = {"project": str(project), "branch": staged["branch"], "message": message,
                       "files": staged["files"][:60], "more_files": max(0, len(staged["files"]) - 60),
                       "changes": redact(staged["stat"][-1500:]), "staged_hash": staged["staged_hash"]}
            risk = "medium"
        elif action == "mysql_write":
            conn = mysql_connection_settings(cfg, body.get("connection"))
            sql = str(body.get("sql") or "").strip()
            if not sql or len(sql) > 20000:
                return _err("The statement is empty or too long.")
            _strip_sql(sql)
            if len([s for s in _strip_sql(sql).split(";") if s.strip()]) != 1:
                return _err("Send one statement at a time.")
            task = "mcp.db_write"
            summary = f"Change data in {conn['name']} ({conn['host']}): {' '.join(sql.split())[:80]}"
            details = {"connection": conn["name"], "host": conn["host"], "database": conn["database"],
                       "sql": sql, "why_it_asks": why or "it changes data"}
            risk = "high"
        elif action == "run":
            project = resolve_project(str(body.get("project") or ""), cfg)
            command = str(body.get("command") or "").strip()
            if not command or len(command) > 4000:
                return _err("The command is empty or too long.")
            task = "mcp.run"
            summary = f"Run “{command[:80]}” in {project.name}"
            details = {"project": str(project), "command": command, "label": str(body.get("label") or "")[:60],
                       "long_running": bool(body.get("long_running")), "why_it_asks": why or "not a known command"}
            risk = "high" if grimoire_needs_approval({}, command) else "medium"
        else:
            return _err("Unknown action.")
    except ToolError as exc:
        return _err(str(exc))
    dec = await _daemon.board.propose(task, summary, details, proposer=PROPOSER, risk=risk, simulated=False)
    return {"decision": {"id": dec["id"], "summary": dec["summary"], "status": dec["status"]}}


@router.get("/decision/{decision_id}")
async def decision(decision_id: str) -> Any:
    if (r := _need_daemon()) is not None:
        return r
    d = _daemon.board.get(decision_id)
    if not d or not str(d["task_type"]).startswith("mcp."):
        return _err("Unknown decision.", 404)
    return {"id": d["id"], "summary": d["summary"], "status": d["status"],
            "result": _results.get(d["id"]) or d.get("executed")}


@router.post("/run")
async def run(request: Request) -> Any:
    """Start a project command (or a non-gated Grimoire macro) in the daemon's runner."""
    if (r := _need_daemon()) is not None:
        return r
    try:
        body = await request.json()
        assert isinstance(body, dict)
    except (ValueError, AssertionError):
        return _err("Send a JSON object.")
    cfg = _cfg()
    try:
        project = resolve_project(str(body.get("project") or ""), cfg)
        if body.get("grimoire_id"):
            entry = _daemon.ext.grimoire.get(str(body["grimoire_id"]))
            if not entry:
                return _err("No saved macro by that name.", 404)
            args = [str(a) for a in body.get("args") or []]
            command = grimoire_command(entry, args)
            if grimoire_needs_approval(entry, command):
                return _err("This macro needs your approval.", 403, needs_approval=True)
            label = f"Grimoire: {entry['description'][:50]}"
            kind = "task"
        else:
            command = str(body.get("command") or "").strip()
            ok, why = project_command_allowed(project, command, cfg)
            if not ok:
                return _err(f"Not run: {why}. Use runner_start to ask for approval.", 403, needs_approval=True)
            label = str(body.get("label") or command)[:60]
            kind = "service" if body.get("long_running") else "task"
    except ToolError as exc:
        return _err(str(exc))
    run_ = await _daemon.ext.runner.start(command, cwd=str(project), label=label, kind=kind)
    _runs.append(run_.id)
    del _runs[:-200]
    return {"run": run_.info()}


@router.get("/runs")
async def runs() -> Any:
    if (r := _need_daemon()) is not None:
        return r
    out = []
    for rid in reversed(_runs):
        run_ = _daemon.ext.runner.runs.get(rid)
        if run_:
            info = run_.info()
            info.pop("tail", None)
            out.append(info)
    return {"runs": out[:40]}


@router.get("/runs/{run_id}")
async def run_detail(run_id: str, lines: int = 60) -> Any:
    if (r := _need_daemon()) is not None:
        return r
    run_ = _daemon.ext.runner.runs.get(run_id) if run_id in _runs else None
    if not run_:
        return _err("No run by that id.", 404)
    info = run_.info()
    info["tail"] = list(run_.tail)[-max(1, min(int(lines), 200)):]
    return {"run": info}
