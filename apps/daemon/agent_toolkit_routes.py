"""Orion Toolkit for coding agents, daemon side: Collision Guard (2), Proof Gate (3) and Baton (1).

One small loop (every TICK_S seconds) reads the agent swarm and feeds it to all three tools:
  * Collision Guard updates its map and announces new conflicts (`collision.warning`, red line in the HUD);
  * Proof Gate starts a check for robots that just said "done" (`proof.result`);
  * Baton offers a hand-off card when an agent hit its usage limit (approval queue, never automatic).

HTTP routes (local only, same origin rules as the rest of the daemon; repo paths must be inside the folders
agents may use, see MCP.md):

    GET  /toolkit/collisions                  the live map: who is changing what, claims, active conflicts
    POST /toolkit/collisions/check            {repo, files?, party?}  -> "is it safe to start?"
    POST /toolkit/collisions/claim            {party, repo, files, branch?, ttl_minutes?, note?}
    POST /toolkit/collisions/release          {claim_id?} or {party, repo?}
    POST /toolkit/proof                       {target?, repo?, claim?, screenshot_url?, wait?}  -> "prove it"
    GET  /toolkit/proof, /toolkit/proof/{id}  recent results / one result
    POST /toolkit/baton                       {from_agent, to_agent, repo?, goal?, notes?, launch?}
    GET  /toolkit/batons, /toolkit/batons/{id}
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable, Optional

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

import agent_swarm
from agent_swarm import AgentSwarm
from baton import Baton, BatonError
from collision_guard import CollisionGuard
from proof_gate import ProofGate

log = logging.getLogger("orion.toolkit")

TICK_S = 5.0
KINDS = {"baton_offer"}   # approval-queue kinds decided by AgentToolkit.decide


def _notify(title: str, body: str) -> None:
    try:
        from crash_healer import _notify as notify   # phone alert + desktop notification
        notify(title, body)
    except Exception:
        pass


class AgentToolkit:
    def __init__(self, daemon: Any, data_dir: Any = None, extra_repos: Any = None) -> None:
        from config import settings
        self.d = daemon
        data_dir = data_dir or settings.data_dir
        publish = getattr(daemon, "_publish_ui", None)
        ext = getattr(daemon, "ext", None)
        self.swarm = AgentSwarm(ext.agent, getattr(ext, "runner", None)) if ext is not None else None
        self._agents: Optional[list[dict[str, Any]]] = None
        self.guard = CollisionGuard(data_dir, publish=publish, notify=_notify,
                                    extra_repos=[settings.repo_root] if extra_repos is None else extra_repos)
        self.proof = ProofGate(data_dir, publish=publish, agents_fn=self.agents)
        mission = getattr(daemon, "mission_control", None)   # launches only through Mission Control, if present
        self.baton = Baton(data_dir, publish=publish, approvals=getattr(daemon, "approvals", None), proof=self.proof,
                           agents_fn=self.agents, launcher=getattr(mission, "launch", None))
        for fn in (self.guard.decorate, self.proof.decorate):
            if fn not in agent_swarm.DECORATORS:
                agent_swarm.DECORATORS.append(fn)
        self._task: Optional[asyncio.Task] = None

    # ------------------------------------------------------------ swarm
    def agents(self) -> list[dict[str, Any]]:
        if self._agents is None and self.swarm is not None:
            try:
                self._agents = self.swarm.list(decorate=False)["agents"]
            except Exception:
                self._agents = []
        return list(self._agents or [])

    async def tick(self) -> None:
        if self.swarm is not None:
            snap = await asyncio.to_thread(self.swarm.list, None, False)
            self._agents = snap["agents"]
        agents = self.agents()
        await self.guard.update(agents)
        self.proof.observe(agents)
        await self.baton.observe(agents)

    def start(self) -> None:
        async def loop() -> None:
            while True:
                await asyncio.sleep(TICK_S)
                try:
                    await self.tick()
                except Exception:
                    log.exception("toolkit tick failed")
        if self._task is None:
            self._task = asyncio.get_running_loop().create_task(loop(), name="agent-toolkit")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            self._task = None
        for fn in (self.guard.decorate, self.proof.decorate):
            if fn in agent_swarm.DECORATORS:
                agent_swarm.DECORATORS.remove(fn)

    async def decide(self, item: dict[str, Any], decision: str, text: str = "") -> None:
        if item.get("kind") == "baton_offer":
            await self.baton.decide(item, decision, text)


# =====================================================================================  routes
def _err(msg: str, status: int = 400) -> JSONResponse:
    return JSONResponse({"error": msg}, status_code=status)


def _allowed_repo(repo: str) -> str:
    """The repo must be inside a folder agents may use (same rule as the MCP tools)."""
    from mcp_tools import McpConfig, resolve_project
    return str(resolve_project(repo, McpConfig.load()))


async def _body(request: Request) -> dict[str, Any]:
    try:
        body = await request.json()
    except ValueError:
        body = None
    if not isinstance(body, dict):
        raise ValueError("Send a JSON object.")
    return body


def _files(v: Any) -> list[str]:
    if isinstance(v, str):
        v = [v]
    return [str(x) for x in (v or []) if str(x).strip()][:200] if isinstance(v, list) else []


def make_router(get_toolkit: Callable[[], Optional[AgentToolkit]]) -> APIRouter:
    from mcp_tools import ToolError
    router = APIRouter(prefix="/toolkit", tags=["toolkit"])

    def tk() -> AgentToolkit:
        t = get_toolkit()
        if t is None:
            raise RuntimeError("Orion is still starting.")
        return t

    async def guarded(fn: Callable[[], Any]) -> Any:
        try:
            return await fn()
        except (ToolError, ValueError, BatonError) as exc:
            return _err(str(exc))
        except RuntimeError as exc:
            return _err(str(exc), 503)

    # ------------------------------------------------------------ Collision Guard
    @router.get("/collisions")
    async def collisions() -> Any:
        async def go() -> Any:
            t = tk()
            return await asyncio.to_thread(t.guard.snapshot, t.agents())
        return await guarded(go)

    @router.post("/collisions/check")
    async def collisions_check(request: Request) -> Any:
        async def go() -> Any:
            b = await _body(request)
            repo = _allowed_repo(str(b.get("repo") or ""))
            t = tk()
            return await asyncio.to_thread(t.guard.check, repo, _files(b.get("files")) or None,
                                           str(b.get("party") or "")[:80], t.agents())
        return await guarded(go)

    @router.post("/collisions/claim")
    async def collisions_claim(request: Request) -> Any:
        async def go() -> Any:
            b = await _body(request)
            repo = _allowed_repo(str(b.get("repo") or ""))
            minutes = float(b.get("ttl_minutes") or 240)
            t = tk()
            c = await asyncio.to_thread(t.guard.claim, str(b.get("party") or ""), repo, _files(b.get("files")),
                                        str(b.get("branch") or ""), minutes * 60, str(b.get("note") or ""))
            check = await asyncio.to_thread(t.guard.check, repo, c["patterns"], c["party"], t.agents())
            return {"claim": c, "safe": check["safe"], "answer": check["answer"], "conflicts": check["conflicts"]}
        return await guarded(go)

    @router.post("/collisions/release")
    async def collisions_release(request: Request) -> Any:
        async def go() -> Any:
            b = await _body(request)
            repo = _allowed_repo(str(b["repo"])) if b.get("repo") else ""
            n = tk().guard.release(str(b.get("claim_id") or ""), str(b.get("party") or ""), repo)
            return {"released": n}
        return await guarded(go)

    # ------------------------------------------------------------ Proof Gate
    @router.post("/proof")
    async def proof(request: Request) -> Any:
        async def go() -> Any:
            b = await _body(request)
            repo = _allowed_repo(str(b["repo"])) if b.get("repo") else ""
            target = str(b.get("target") or b.get("agent") or b.get("branch") or "")[:200]
            kw = {"target": target, "repo": repo, "claim": b.get("claim"),
                  "screenshot": str(b.get("screenshot_url") or "") or None}
            t = tk()
            if b.get("wait") is False:
                asyncio.get_running_loop().create_task(t.proof.prove_and_announce(**kw))
                return {"started": True, "message": "Orion is checking it now; the result arrives as proof.result."}
            return await t.proof.prove_and_announce(**kw)
        return await guarded(go)

    @router.get("/proof")
    async def proof_recent(limit: int = 20) -> Any:
        async def go() -> Any:
            return {"results": tk().proof.recent(max(1, min(limit, 60)))}
        return await guarded(go)

    @router.get("/proof/{result_id}")
    async def proof_one(result_id: str) -> Any:
        async def go() -> Any:
            r = tk().proof.get(result_id)
            return r if r else _err("No proof result with that id.", 404)
        return await guarded(go)

    # ------------------------------------------------------------ Baton
    @router.post("/baton")
    async def baton(request: Request) -> Any:
        async def go() -> Any:
            b = await _body(request)
            repo = _allowed_repo(str(b["repo"])) if b.get("repo") else ""
            rec = await tk().baton.handoff(str(b.get("from_agent") or ""), str(b.get("to_agent") or ""), repo=repo,
                                           goal=str(b.get("goal") or "")[:2000], notes=str(b.get("notes") or "")[:4000],
                                           reason=str(b.get("reason") or "")[:200] or "You asked for a hand-off.",
                                           launch=bool(b.get("launch")))
            return rec
        return await guarded(go)

    @router.get("/batons")
    async def batons(limit: int = 20) -> Any:
        async def go() -> Any:
            return {"batons": tk().baton.recent(max(1, min(limit, 50)))}
        return await guarded(go)

    @router.get("/batons/{baton_id}")
    async def baton_one(baton_id: str) -> Any:
        async def go() -> Any:
            r = tk().baton.get(baton_id)
            return r if r else _err("No hand-off with that id.", 404)
        return await guarded(go)

    return router
