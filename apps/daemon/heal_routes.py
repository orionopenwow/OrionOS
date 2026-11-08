"""HTTP routes for the crash auto-healer (crash_healer.py).

  GET  /heal                 recent crashes and what Orion is doing about each, plus settings
  POST /heal/{id}/retry      try again (also works for projects that aren't opted in)
  POST /heal/test            paste a crash/trace by hand; {"run": true} starts the fix pipeline for it
  POST /heal/settings        turn it on/off, limits, and per-project opt-in

Approving a fix is not here: it's a boardroom decision, signed on the boardroom VM or a paired phone.
"""
from __future__ import annotations

from typing import Any, Callable, Optional

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel


class TraceRequest(BaseModel):
    text: str
    project: Optional[str] = None
    command: str = ""
    run: bool = False


class ProjectSetting(BaseModel):
    path: str
    enabled: Optional[bool] = None
    test_command: Optional[str] = None
    python: Optional[str] = None
    image: Optional[str] = None


class HealSettings(BaseModel):
    enabled: Optional[bool] = None
    max_attempts: Optional[int] = None
    daily_model_calls: Optional[int] = None
    max_new_per_hour: Optional[int] = None
    test_timeout_s: Optional[int] = None
    use_sandbox: Optional[bool] = None
    project: Optional[ProjectSetting] = None


def make_router(get_healer: Callable[[], Any]) -> APIRouter:
    router = APIRouter()

    @router.get("/heal")
    async def heal_state() -> dict[str, Any]:
        return get_healer().snapshot()

    @router.post("/heal/settings")
    async def heal_settings(req: HealSettings) -> dict[str, Any]:
        changes = req.model_dump(exclude_none=True)
        if req.project is not None:
            changes["project"] = req.project.model_dump()
        return {"settings": get_healer().update_settings(**changes)}

    @router.post("/heal/test")
    async def heal_test(req: TraceRequest) -> Any:
        if len(req.text) > 200_000:
            return JSONResponse({"error": "That's too much text (200 KB max)."}, status_code=413)
        rec = get_healer().observe_text(req.text, cwd=req.project, command=req.command, source="manual",
                                        force=req.run)
        if rec is None:
            return JSONResponse({"error": "Orion didn't find a crash (stack trace or panic) in that text, "
                                          "or the hourly limit for new crashes was reached."}, status_code=422)
        return {"crash": rec}

    @router.post("/heal/{crash_id}/retry")
    async def heal_retry(crash_id: str) -> Any:
        try:
            return {"crash": get_healer().retry(crash_id)}
        except KeyError:
            return JSONResponse({"error": "unknown crash"}, status_code=404)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=409)

    return router
