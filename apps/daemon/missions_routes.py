"""HTTP routes and daemon wiring for Mission Control (missions.py).

    GET  /missions                      board: missions, installed agents, templates, routing version
    POST /missions                      {title, description?, repo?, agent?, budget_usd?, time_limit_min?, race?, template?}
    POST /missions/swarm                {title, tasks?: [..], size?, concurrency?, budget_usd?, repo?, agent?}
    POST /missions/speculate            {repo, patches: [..]}  test candidate patches in throwaway worktrees
    GET  /missions/{id}                 one mission with its diff and log tail
    GET  /missions/{id}/log?since=N     log lines from N on
    POST /missions/{id}/{op}            pause | resume | stop | approve_review | reject

approve_review never merges: it asks for a signed boardroom decision ("mission.merge"); the merge runs only after
your signature (phone fingerprint or the boardroom), like every other merge.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable, Optional

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

import missions
from missions import KINDS, MissionControl, MissionError

log = logging.getLogger("orion.missions.routes")


def attach(daemon: Any) -> MissionControl:
    """Make (or reuse) Mission Control for the daemon and hook it into the boardroom, Baton and the swarm."""
    mc = getattr(daemon, "mission_control", None)
    if isinstance(mc, MissionControl):
        return mc
    from config import settings
    toolkit = getattr(daemon, "agent_toolkit", None)
    orch = getattr(daemon, "orchestrator", None)
    mc = MissionControl(settings.data_dir, publish=getattr(daemon, "_publish_ui", None),
                        approvals=getattr(daemon, "approvals", None), board=getattr(daemon, "board", None),
                        proof=getattr(toolkit, "proof", None), meter=getattr(orch, "meter", None),
                        default_repo=settings.repo_root)
    daemon.mission_control = mc
    missions.install(mc)
    missions.register_task_type()
    board = getattr(daemon, "board", None)
    if board is not None:
        board.executors[missions.TASK_TYPE] = mc.exec_merge
        board.slow_executors.add(missions.TASK_TYPE)   # [review-fix 5] git worktree + merge + Secret Shield: a thread
        if mc.on_decided not in board.on_decided:
            board.on_decided.append(mc.on_decided)
    baton = getattr(toolkit, "baton", None)
    if baton is not None:
        baton.launcher = mc.launch          # Baton: hand-offs start the next agent through Mission Control
    try:
        import agent_swarm
        if mc.decorate not in agent_swarm.DECORATORS:
            agent_swarm.DECORATORS.append(mc.decorate)
    except Exception:
        log.debug("swarm decorator not added", exc_info=True)
    return mc


def start(daemon: Any) -> None:
    attach(daemon).start()


async def stop(daemon: Any) -> None:
    mc = getattr(daemon, "mission_control", None)
    if isinstance(mc, MissionControl):
        await mc.stop()
        try:
            import agent_swarm
            if mc.decorate in agent_swarm.DECORATORS:
                agent_swarm.DECORATORS.remove(mc.decorate)
        except Exception:
            pass


async def decide_card(daemon: Any, item: dict[str, Any], decision: str, text: str = "") -> None:
    """Approval-queue decision on a mission card. Approve only asks for the signed merge."""
    mc = attach(daemon)
    kind = item.get("kind")
    if decision == "reject":
        if kind == missions.CARD_KIND and item.get("mission_id"):
            mc.act(item["mission_id"], "reject")
        elif kind in (missions.RACE_CARD_KIND, missions.SWARM_CARD_KIND):
            for mid in item.get("candidates") or []:
                mc.act(mid, "reject")
        upd = mc.approvals.update(item["id"], status="rejected") if mc.approvals else None
        if upd and mc.publish:
            await mc.publish({"event": "APPROVAL_RESOLVED", "item": upd})
        return
    if kind == missions.SWARM_CARD_KIND:
        notes = []
        for mid in item.get("candidates") or []:
            m = mc.get(mid)
            if m and (m.get("checks") or {}).get("verdict") in ("green", "skipped"):
                notes.append(mc.act(mid, "approve_review")["message"])
        final = f"Asked to merge {len(notes)} part(s); each needs your signature."
    else:
        target = (text.strip() if decision == "edit" and text.strip() else "") or item.get("mission_id") or item.get("draft")
        res = mc.act(str(target or ""), "approve_review")
        final = res["message"]
        if not res.get("ok"):
            upd = mc.approvals.update(item["id"], error=final) if mc.approvals else None
            if upd and mc.publish:
                await mc.publish({"event": "APPROVAL_REQUIRED", "item": upd})
            return
    upd = mc.approvals.update(item["id"], status="sent", final=final, error=None) if mc.approvals else None
    if upd and mc.publish:
        await mc.publish({"event": "APPROVAL_RESOLVED", "item": upd})


def _err(msg: str, status: int = 400) -> JSONResponse:
    return JSONResponse({"error": msg}, status_code=status)


def _repo_ok(repo: Optional[str], mc: MissionControl) -> Optional[str]:
    """A repo given over HTTP must be Orion's own or inside a folder agents may use (mcp.json roots)."""
    if not repo or str(repo).strip().lower() == "orion":
        return None
    from pathlib import Path
    if mc.default_repo and Path(str(repo)).expanduser().resolve() == mc.default_repo.resolve():
        return str(mc.default_repo)
    from mcp_tools import McpConfig, resolve_project
    return str(resolve_project(str(repo), McpConfig.load()))


async def _body(request: Request) -> dict[str, Any]:
    try:
        body = await request.json()
    except ValueError:
        body = None
    if not isinstance(body, dict):
        raise MissionError("Send a JSON object.")
    return body


def _float(v: Any) -> Optional[float]:
    if v in (None, ""):
        return None
    return float(v)


def make_router(get_daemon: Callable[[], Any]) -> APIRouter:
    router = APIRouter()

    def mc() -> MissionControl:
        return attach(get_daemon())

    @router.get("/missions")
    async def board() -> dict[str, Any]:
        c = mc()
        rules = await asyncio.to_thread(c.routing)
        return {"missions": c.list(), "agents": await asyncio.to_thread(c.detect), "templates": missions.TEMPLATES,
                "states": list(missions.STATES),
                "routing": {"version": rules.get("version"), "source": rules.get("source"),
                            "tasks": sorted((rules.get("tasks") or {}).keys())}}

    @router.post("/missions")
    async def create(request: Request) -> Any:
        c = mc()
        try:
            b = await _body(request)
            repo = await asyncio.to_thread(_repo_ok, b.get("repo"), c)
            m = await asyncio.to_thread(
                c.create, str(b.get("title") or ""), repo, b.get("agent") or None, _float(b.get("budget_usd")),
                int(b.get("race") or 1), "hud", description=str(b.get("description") or ""),
                time_limit_min=_float(b.get("time_limit_min")), template=b.get("template") or None)
        except (MissionError, ValueError, TypeError) as exc:
            return _err(str(exc))
        except Exception as exc:   # mcp_tools.ToolError (repo outside the allowed folders)
            return _err(str(exc))
        return m

    @router.post("/missions/swarm")
    async def swarm(request: Request) -> Any:
        c = mc()
        try:
            b = await _body(request)
            repo = await asyncio.to_thread(_repo_ok, b.get("repo"), c)
            tasks = b.get("tasks") if isinstance(b.get("tasks"), list) else None
            return await asyncio.to_thread(
                c.spawn_swarm, str(b.get("title") or "Swarm"), tasks, repo, b.get("agent") or None,
                int(b["size"]) if b.get("size") else None, int(b["concurrency"]) if b.get("concurrency") else None,
                _float(b.get("budget_usd")), "hud", str(b.get("description") or ""))
        except Exception as exc:
            return _err(str(exc))

    @router.post("/missions/speculate")
    async def speculate(request: Request) -> Any:
        c = mc()
        try:
            b = await _body(request)
            repo = await asyncio.to_thread(_repo_ok, b.get("repo"), c) or str(c.default_repo)
            patches = [str(p) for p in b.get("patches") or [] if str(p).strip()]
            if not patches:
                return _err("Send at least one patch.")
            return {"results": await c.speculate(repo, patches)}
        except Exception as exc:
            return _err(str(exc))

    @router.get("/missions/{mission_id}")
    async def one(mission_id: str) -> Any:
        d = mc().detail(mission_id)
        return d if d is not None else _err("There's no mission with that id.", 404)

    @router.get("/missions/{mission_id}/log")
    async def log_(mission_id: str, since: int = 0) -> Any:
        try:
            return mc().read_log(mission_id, max(0, int(since)))
        except KeyError:
            return _err("There's no mission with that id.", 404)

    @router.post("/missions/{mission_id}/{op}")
    async def op(mission_id: str, op: str) -> Any:
        res = mc().act(mission_id, op)
        return res if res.get("ok") else JSONResponse(res, status_code=400)

    return router


__all__ = ["KINDS", "attach", "start", "stop", "decide_card", "make_router"]
