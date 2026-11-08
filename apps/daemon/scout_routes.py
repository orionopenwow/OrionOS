"""Video Scout HTTP routes, chat hooks and the phone share route (see video_scout.py).

  POST /scout                {url | urls | text, force?}   queue links (also playlist/channel/"ytsearchN:words")
  GET  /scout/videos         the cards, newest first
  GET  /scout/video/{id}     one card with transcript, on-screen text, mentions and the view history
  GET  /scout/leaderboard    ?days=7&format=json|md|csv
  POST /scout/leaderboard/ideas {path?}   append this week's board to <project>/IDEAS.md
  GET/POST /scout/settings   rate, on-screen text, comments, cookies (opt-in), model
  POST /scout/catalogue/refresh           look for new addons on GitHub (needs GITHUB_TOKEN)
  POST /phone/scout          (lab phone port, read key) the Android share sheet sends links here

Main wiring (main.py): `self.scout = scout_routes.attach(self)` and `app.include_router(scout_routes.router)`.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import uuid
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, PlainTextResponse
from pydantic import BaseModel

import video_scout as vs

log = logging.getLogger("orion.scout.routes")

router = APIRouter()
_scout: Optional["ScoutHub"] = None
PHONE_MAX_CHARS = 4000


class ScoutHub:
    """The VideoScout plus the daemon's ways of answering (chat stream, phone alert, Telegram)."""

    def __init__(self, scout: vs.VideoScout, daemon: Any = None) -> None:
        self.scout = scout
        self.d = daemon

    def start(self) -> None:
        self.scout.start()

    async def stop(self) -> None:
        await self.scout.stop()

    # ------------------------------------------------------------------ replies
    def _reply_for(self, source: str, chat_id: Optional[str] = None):
        async def to_chat(card: dict[str, Any]) -> None:
            if self.d is None or card.get("status") in ("expanded",):
                return
            await self.d.bus.publish({
                "event": "ORCHESTRATOR_RESPONSE", "request_id": uuid.uuid4().hex[:12], "trigger": "user", "prompt": "",
                "response": {"intent": "summary", "tier": "local", "summary": vs.reply_text(card), "actions": [],
                             "model": "video-scout", "latency_ms": 0}})

        async def to_phone(card: dict[str, Any]) -> None:
            if card.get("status") == "expanded":
                return
            import alerts
            title = card.get("title") or card.get("url", "")
            alerts.emit("life", f"Video Scout: {title}"[:120], vs.reply_text(card)[:900], ref=card.get("id"))

        async def to_telegram(card: dict[str, Any]) -> None:
            tg = getattr(self.d, "telegram", None)
            if tg is not None and card.get("status") != "expanded":
                await tg.send_message(vs.reply_text(card)[:3500], chat_id=chat_id, parse_mode="")

        return {"user": to_chat, "phone": to_phone, "telegram": to_telegram}.get(source)

    # ------------------------------------------------------------------ hooks
    async def chat(self, text: str, source: str, chat_id: Optional[str] = None) -> Optional[str]:
        """converse(): returns a reply when the text was a scout command or a video link."""
        if source not in ("user", "phone", "telegram", "voice"):
            return None
        if source == "voice" and not re.match(r"^\W*(orion\W+)?scout\b", text, re.I):
            return None   # spoken words never carry links; only "scout top" / "scout status" make sense
        return await self.scout.chat(text, source, reply=self._reply_for("user" if source == "voice" else source, chat_id))

    async def handle_prompt(self, text: str, source: str) -> bool:
        """HUD chat (USER_PROMPT): answers in the chat stream and returns True when handled."""
        reply = await self.chat(text, source)
        if reply is None or self.d is None:
            return reply is not None
        await self.d.bus.publish({
            "event": "ORCHESTRATOR_RESPONSE", "request_id": uuid.uuid4().hex[:12], "trigger": source, "prompt": text,
            "response": {"intent": "automation", "tier": "local", "summary": reply, "actions": [],
                         "model": "video-scout", "latency_ms": 0}})
        return True

    async def handle_telegram(self, text: str, chat_id: str) -> bool:
        reply = await self.chat(text, "telegram", chat_id)
        if reply is None:
            return False
        tg = getattr(self.d, "telegram", None)
        if tg is not None:
            await tg.send_message(reply, chat_id=chat_id, parse_mode="")
        return True

    # ------------------------------------------------------------------ phone (Boardroom.remote_hooks)
    def phone_hook(self, app: Any, phone_auth: Any, json_body: Any) -> None:
        @app.post("/phone/scout")
        async def phone_scout(request: Request):
            who, err = await phone_auth(request, "read")   # read-only scouting: no fingerprint needed
            if err is not None:
                return err
            data = await json_body(request)
            text = str((data or {}).get("url") or (data or {}).get("text") or "")[:PHONE_MAX_CHARS]
            urls = vs.find_links(text) + vs.find_collections(text)
            if not urls:
                return JSONResponse({"error": "No TikTok, YouTube or Instagram Reels link in what you shared."},
                                    status_code=400)
            r = self.scout.submit(urls, "phone", reply=self._reply_for("phone"))
            return {"reply": vs.VideoScout._queued_reply(r) + " You'll get an alert when it's done.",
                    "queued": len(r["queued"]), "cached": r["cached"]}


def attach(daemon: Any) -> ScoutHub:
    """Builds the scout for the running daemon and registers the phone route. Call once from OrionDaemon.__init__."""
    global _scout
    from config import settings
    orch = daemon.orchestrator

    async def llm(prompt: str, schema: dict[str, Any]) -> dict[str, Any]:
        mode = hub.scout.settings().get("llm", "local")
        if mode == "local" and await orch.ollama_available():
            data, _ = await orch.ollama_json(prompt, schema, timeout=180)
            return data if isinstance(data, dict) else json.loads(data)
        text, _ = await orch.frontier_text("You summarise videos. Reply with one JSON object only.", prompt,
                                           json_mode=True, max_tokens=1200, temperature=0.2)
        m = re.search(r"\{.*\}", text, re.S)
        return json.loads(m.group(0) if m else text)

    scout = vs.VideoScout(settings.data_dir, llm=llm, publish=daemon._publish_ui, vault_dir=settings.data_dir / "vault")
    hub = ScoutHub(scout, daemon)
    daemon.board.remote_hooks.append(hub.phone_hook)
    _scout = hub
    return hub


def install(hub: ScoutHub) -> None:
    """Tests: use this hub for the HTTP routes."""
    global _scout
    _scout = hub


def _hub() -> ScoutHub:
    if _scout is None:
        raise RuntimeError("Video Scout isn't attached")
    return _scout


# ---------------------------------------------------------------------------------------------- HTTP
class ScoutRequest(BaseModel):
    url: Optional[str] = None
    urls: Optional[list[str]] = None
    text: Optional[str] = None
    file: Optional[str] = None
    force: bool = False


@router.post("/scout")
async def scout_submit(req: ScoutRequest) -> Any:
    hub = _hub()
    urls: list[str] = []
    for u in ([req.url] if req.url else []) + list(req.urls or []):
        u = u.strip()
        if vs._YTSEARCH.match(u):
            urls.append(u)
        else:
            urls += vs.find_links(u) + vs.find_collections(u)
    if req.text:
        urls += vs.find_links(req.text) + vs.find_collections(req.text)
    if req.file:
        try:
            urls += vs.read_link_file(Path(req.file).expanduser())
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
    if not urls:
        return JSONResponse({"error": "No TikTok, YouTube/Shorts or Instagram Reels link found."}, status_code=400)
    r = hub.scout.submit(urls, "user", reply=hub._reply_for("user"), force=req.force)
    return {**r, "message": vs.VideoScout._queued_reply(r)}


@router.get("/scout/videos")
async def scout_videos(limit: int = 50) -> Any:
    hub = _hub()
    return {"videos": hub.scout.cards(limit), "status": hub.scout.status()}


@router.get("/scout/video/{vid:path}")
async def scout_video(vid: str) -> Any:
    d = _hub().scout.detail(vid)
    if d is None:
        return JSONResponse({"error": "not scouted"}, status_code=404)
    return d


@router.get("/scout/leaderboard")
async def scout_leaderboard(days: float = 7, format: str = "json") -> Any:
    s = _hub().scout
    days = max(0.0, min(days, 365.0))
    if format == "md":
        return PlainTextResponse(s.export_markdown(days), media_type="text/markdown")
    if format == "csv":
        return PlainTextResponse(s.export_csv(days), media_type="text/csv",
                                 headers={"Content-Disposition": "attachment; filename=video-scout-leaderboard.csv"})
    return s.leaderboard(days)


class IdeasRequest(BaseModel):
    path: Optional[str] = None
    days: float = 7


@router.post("/scout/leaderboard/ideas")
async def scout_to_ideas(req: IdeasRequest) -> Any:
    from config import settings
    try:
        path = _hub().scout.send_to_ideas(Path(req.path or settings.repo_root), req.days)
    except (ValueError, OSError) as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    return {"saved": str(path)}


@router.get("/scout/settings")
async def scout_settings() -> Any:
    s = _hub().scout
    return {"settings": s.settings(), "status": s.status(), "prompt_file": str(s.prompt_path),
            "catalogue_file": str(s.catalogue.addons_path), "keywords_file": str(s.catalogue.keywords_path)}


@router.post("/scout/settings")
async def scout_settings_set(request: Request) -> Any:
    try:
        changes = await request.json()
        if not isinstance(changes, dict):
            raise ValueError("send an object of settings")
        return {"settings": _hub().scout.set_settings(changes)}
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)


@router.post("/scout/catalogue/refresh")
async def scout_catalogue_refresh() -> Any:
    return await _hub().scout.catalogue.refresh_from_github()
