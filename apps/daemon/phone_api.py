"""Orion Mobile v0.2 routes on the lab's phone port (9121), next to /phone/pending and /phone/verdict.

Every route needs a request signed by a paired phone (phones.py). Which key:
  read key (no fingerprint): the alert feed, feedback, alert settings, chat, voice, cards, "I did it"
  sign key (fingerprint):    "Orion does it" on a card (Orion clicks on your PC), and approvals (boardroom.py)

Chat and voice never approve anything: approvals only happen through /phone/verdict with the fingerprint key.
Missions ("send work to the PC") are added here too, from phone_missions.py; chat and voice check
"have the PC build ..." first and queue a mission (never merged, reviews need the fingerprint key).
"""
from __future__ import annotations

import base64
import logging
from typing import Any, Awaitable, Callable, Optional, Protocol

from fastapi import Request
from fastapi.responses import JSONResponse, Response

log = logging.getLogger("orion.phone_api")

VOICE_MAX_BYTES = 3 * 1024 * 1024   # about 3 minutes of phone-recorded AAC
CHAT_MAX_CHARS = 2000


class Hub(Protocol):
    alerts: Any
    cards: Any
    quick: Any

    async def converse(self, text: str, source: str) -> str: ...
    async def transcribe(self, audio: bytes) -> Optional[str]: ...
    async def speak(self, text: str) -> Optional[bytes]: ...


def register(hub: Hub) -> Callable[..., None]:
    """Returns a hook for Boardroom.remote_hooks: hook(app, phone_auth, json_body)."""

    def hook(app, phone_auth, json_body) -> None:
        # ------------------------------------------------------------ alerts (Step 4b)
        @app.get("/phone/feed")
        async def feed(request: Request, since: int = 0, wait: int = 0):
            who, err = await phone_auth(request, "read")
            if err is not None:
                return err
            return await hub.alerts.wait_feed(max(0, since), float(min(max(wait, 0), 55)))

        @app.post("/phone/feedback")
        async def feedback(request: Request):
            who, err = await phone_auth(request, "read")
            if err is not None:
                return err
            data = await json_body(request)
            if data is None or not isinstance(data.get("events"), list):
                return JSONResponse({"error": "bad request"}, status_code=400)
            return {"changes": hub.alerts.feedback([e for e in data["events"] if isinstance(e, dict)])}

        @app.post("/phone/alert-settings")
        async def alert_settings(request: Request):
            who, err = await phone_auth(request, "read")
            if err is not None:
                return err
            data = await json_body(request)
            if data is None:
                return JSONResponse({"error": "bad request"}, status_code=400)
            try:
                hub.alerts.set(str(data.get("type", "")), data.get("level"),
                               data.get("locked") if isinstance(data.get("locked"), bool) else None)
            except ValueError as exc:
                return JSONResponse({"error": str(exc)}, status_code=400)
            return {"policy": hub.alerts.summary()}

        # ------------------------------------------------------------ chat and voice (Step 4d)
        @app.post("/phone/chat")
        async def chat(request: Request):
            who, err = await phone_auth(request, "read")
            if err is not None:
                return err
            data = await json_body(request)
            text = str((data or {}).get("text", "")).strip()[:CHAT_MAX_CHARS]
            if not text:
                return JSONResponse({"error": "say something"}, status_code=400)
            reply = await _missions_words(text) or await hub.converse(text, "phone")   # phone_missions.py
            out: dict[str, Any] = {"heard": text, "reply": reply}
            if (data or {}).get("speak"):
                audio = await hub.speak(reply)
                if audio:
                    out["audio_b64"] = base64.b64encode(audio).decode()
            return out

        @app.post("/phone/voice")
        async def voice(request: Request):
            who, err = await phone_auth(request, "read", VOICE_MAX_BYTES)
            if err is not None:
                return err
            audio = await request.body()
            if not audio:
                return JSONResponse({"error": "no audio"}, status_code=400)
            heard = await hub.transcribe(audio)
            if heard is None:
                return JSONResponse({"error": getattr(hub, "voice_missing", None) or (
                    "Voice isn't installed on the lab yet. On the lab: orion-ctl setup "
                    "and approve 'Voice for Orion'.")}, status_code=503)
            if not heard.strip():
                return {"heard": "", "reply": "I didn't catch that."}
            reply = await _missions_words(heard) or await hub.converse(heard, "phone")   # phone_missions.py
            out: dict[str, Any] = {"heard": heard, "reply": reply}
            audio_out = await hub.speak(reply)
            if audio_out:
                out["audio_b64"] = base64.b64encode(audio_out).decode()
            return out

        @app.get("/phone/quick")
        async def quick(request: Request):
            who, err = await phone_auth(request, "read")
            if err is not None:
                return err
            return {"commands": sorted(hub.quick.commands().keys())}

        # ------------------------------------------------------------ next-step cards (Step 4c)
        @app.get("/phone/cards")
        async def cards(request: Request):
            who, err = await phone_auth(request, "read")
            if err is not None:
                return err
            return {"cards": hub.cards.open_cards()}

        @app.get("/phone/card-image/{cid}")
        async def card_image(cid: str, request: Request):
            who, err = await phone_auth(request, "read")
            if err is not None:
                return err
            try:
                data = hub.cards.image(cid)
            except KeyError:
                data = None
            if not data:
                return JSONResponse({"error": "no picture (withheld or older than 24 hours)"}, status_code=404)
            return Response(data, media_type="image/jpeg", headers={"Cache-Control": "no-store"})

        @app.post("/phone/card/done")
        async def card_done(request: Request):
            who, err = await phone_auth(request, "read")
            if err is not None:
                return err
            data = await json_body(request)
            try:
                return {"card": hub.cards.done(str((data or {}).get("id", "")))}
            except KeyError:
                return JSONResponse({"error": "unknown step"}, status_code=404)

        @app.post("/phone/card/do")
        async def card_do(request: Request):
            who, err = await phone_auth(request, "sign")   # Orion acting on your PC: fingerprint
            if err is not None:
                return err
            data = await json_body(request)
            try:
                return {"card": hub.cards.do(str((data or {}).get("id", "")))}
            except KeyError:
                return JSONResponse({"error": "unknown step"}, status_code=404)
            except ValueError as exc:
                return JSONResponse({"error": str(exc)}, status_code=409)

        # ------------------------------------------------------------ send work to the PC (phone_missions.py)
        if _phone_missions() is not None:
            _phone_missions().hook(app, phone_auth, json_body)
        try:  # [testcenter] the Test Center on the phone: /phone/tests (phone_tests.py)
            __import__("phone_tests").hook(hub, app, phone_auth, json_body)
        except Exception as exc:  # [testcenter] a broken optional part never takes the phone routes down
            log.warning("Phone Test Center routes not added: %s", exc)  # [testcenter]

    return hook


def _phone_missions() -> Any:
    """phone_missions.py, or None on a trimmed install (Pocket Orion doesn't copy it)."""
    try:
        import phone_missions
        return phone_missions
    except ImportError:
        return None


async def _missions_words(text: str) -> Optional[str]:
    """"Orion, have the PC build ..." by chat or voice queues a mission; anything else returns None."""
    pm = _phone_missions()
    if pm is None:
        return None
    try:
        return await pm.from_words(text)
    except Exception as exc:
        log.warning("Mission from words failed: %s", exc)
        return None
