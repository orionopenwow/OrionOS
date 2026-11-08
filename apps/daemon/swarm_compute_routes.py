"""HTTP routes for the compute meter and the agent swarm.

    GET  /compute                      tokens + estimated dollars per model, today and this session, tips
    GET  /agents/swarm                 every agent seen recently (robots in the HUD)
    POST /agents/swarm/{id}/{action}   action = open | pause | resume | stop (pause/stop: Orion-started only)
"""
from __future__ import annotations

import asyncio
from typing import Any, Callable

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from agent_swarm import AgentSwarm


def make_router(get_daemon: Callable[[], Any]) -> APIRouter:
    router = APIRouter()
    swarms: dict[int, AgentSwarm] = {}

    def swarm() -> AgentSwarm:
        d = get_daemon()
        key = id(d)
        if key not in swarms:
            swarms.clear()
            swarms[key] = AgentSwarm(d.ext.agent, getattr(d.ext, "runner", None))
        return swarms[key]

    @router.get("/compute")
    async def compute() -> dict[str, Any]:
        return get_daemon().orchestrator.meter.snapshot()

    @router.get("/agents/swarm")
    async def agents_swarm() -> dict[str, Any]:
        return await asyncio.to_thread(swarm().list)

    @router.post("/agents/swarm/{agent_id}/{action}")
    async def agents_swarm_action(agent_id: str, action: str) -> Any:
        ok, message = await swarm().act(agent_id, action)
        if not ok:
            return JSONResponse({"ok": False, "message": message}, status_code=400)
        return {"ok": True, "message": message}

    return router
