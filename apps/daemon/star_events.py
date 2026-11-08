"""Small stars for the core: something you started finished while you were looking elsewhere.

POST /events/star  {"source": "terminal", "ok": true, "seconds": 74, "title": "cargo build --release"}

The OrionOS shell (os/theme/shell/orion.bash) sends one when a command that ran longer than 30 s finishes while
its terminal isn't the focused window (it also shows a notification). The daemon only passes it on to the HUD as a
STAR event, which draws a small star falling into the disk (green-ish for done, ember for a failure). Nothing is
stored and nothing runs because of it.
"""
from __future__ import annotations

from typing import Any, Callable

from fastapi import APIRouter
from pydantic import BaseModel, Field


class Star(BaseModel):
    source: str = Field("terminal", max_length=32)
    ok: bool = True
    seconds: int = Field(0, ge=0, le=10_000_000)
    title: str = Field("", max_length=160)


def make_router(get_daemon: Callable[[], Any]) -> APIRouter:
    router = APIRouter()

    @router.post("/events/star")
    async def star(s: Star) -> dict[str, Any]:
        event = {"event": "STAR", "source": s.source, "ok": s.ok, "seconds": s.seconds, "title": s.title}
        await get_daemon().bus.publish(event, record=False)
        return {"ok": True}

    return router
