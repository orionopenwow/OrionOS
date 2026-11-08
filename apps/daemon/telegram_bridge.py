"""Telegram Integration Bridge for Orion.

Enables Orion to:
  1. Text task results, code suggestions, summaries, and screen captures directly to Telegram.
  2. Receive commands & questions from the user's phone via Telegram long polling.

Who counts as "the user" (review item B2): only the chat that paired. Orion never adopts the first person who
happens to message the bot. To pair, press "Pair Telegram" in the HUD (Control > Pair) or run
`orion-ctl telegram pair`; Orion shows a one-time code (10 minutes, 5 tries) and you send `/pair CODE` to your bot.
The paired chat is kept in data_dir/telegram_owner.json (it wins over TELEGRAM_CHAT_ID). Until then every message
is treated like anyone else's: it can land in the inbox as text, but it can't run commands, see the screen or
talk to the orchestrator.

    POST /telegram/pair      -> {"code", "expires_in"}     (409 when no bot token is set)
    GET  /telegram/pair      -> {"paired", "pending", "running"}
    POST /telegram/unpair    -> {"paired": false}
"""
from __future__ import annotations

import asyncio
import hmac
import io
import json
import logging
import os
import secrets
import time
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional

import httpx

log = logging.getLogger("orion.telegram")

PAIR_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
PAIR_TTL_S = 600.0
PAIR_MAX_TRIES = 5


def load_owner(path: Optional[Path]) -> str:
    if not path:
        return ""
    try:
        d = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    return str(d.get("chat_id") or "").strip() if isinstance(d, dict) else ""


def save_owner(path: Optional[Path], chat_id: str) -> None:
    if not path:
        return
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump({"chat_id": chat_id, "paired_at": time.time()} if chat_id else {}, f)
    os.replace(tmp, path)


class TelegramBridge:
    def __init__(
        self,
        token: str,
        chat_id: str,
        on_user_message: Optional[Callable[[str, str], Awaitable[None]]] = None,
        on_foreign_message: Optional[Callable[[dict[str, Any]], Awaitable[None]]] = None,
        owner_file: Optional[Path] = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.token = token.strip()
        self.owner_file = Path(owner_file) if owner_file else None
        # The chat that paired wins over TELEGRAM_CHAT_ID; nobody is adopted automatically.
        self.chat_id = load_owner(self.owner_file) or chat_id.strip()
        self.clock = clock
        self._pair_code: Optional[str] = None
        self._pair_expires = 0.0
        self._pair_tries = 0
        self.on_user_message = on_user_message
        # V2 Comms hub: messages from chats other than the owner's command chat.
        self.on_foreign_message = on_foreign_message
        self._http: Optional[httpx.AsyncClient] = None
        self._polling_task: Optional[asyncio.Task] = None
        self._last_update_id = 0
        self.enabled = bool(self.token and self.chat_id)

    @property
    def api_url(self) -> str:
        return f"https://api.telegram.org/bot{self.token}"

    @property
    def is_running(self) -> bool:
        return self._polling_task is not None and not self._polling_task.done()

    async def start(self) -> bool:
        if not self.token:
            log.info("Telegram bridge disabled (no TELEGRAM_BOT_TOKEN)")
            return False

        self._http = httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=5.0))
        try:
            # Verify bot credentials
            res = await self._http.get(f"{self.api_url}/getMe")
            if res.status_code == 200:
                bot_info = res.json().get("result", {})
                username = bot_info.get("username", "UnknownBot")
                log.info("Telegram bot connected as @%s (default chat_id=%s)", username, self.chat_id)
                self.enabled = True

                # Start polling loop if message callback provided
                if self.on_user_message:
                    self._polling_task = asyncio.create_task(self._poll_updates(), name="orion-telegram-poll")
                return True
            else:
                log.warning("Telegram authentication failed: %s", res.text)
                return False
        except Exception as exc:
            log.warning("Failed to initialize Telegram bridge: %s", exc)
            return False

    async def stop(self) -> None:
        if self._polling_task:
            self._polling_task.cancel()
            self._polling_task = None
        if self._http:
            await self._http.aclose()
            self._http = None

    async def send_message(self, text: str, chat_id: Optional[str] = None, parse_mode: str = "Markdown") -> bool:
        """Send a text message to Telegram."""
        target_chat = chat_id or self.chat_id
        if not self.token or not target_chat:
            log.warning("Cannot send Telegram message: token or chat_id missing")
            return False

        client = self._http or httpx.AsyncClient(timeout=10.0)
        try:
            # Escape or fallback if markdown fails
            payload = {
                "chat_id": target_chat,
                "text": text[:4000],
                "parse_mode": parse_mode,
            }
            res = await client.post(f"{self.api_url}/sendMessage", json=payload)
            if res.status_code != 200:
                # Retry without markdown in case of formatting parse error
                payload["parse_mode"] = ""
                res = await client.post(f"{self.api_url}/sendMessage", json=payload)
            return res.status_code == 200
        except Exception as exc:
            log.error("Telegram send_message failed: %s", exc)
            return False
        finally:
            if not self._http:
                await client.aclose()

    async def send_photo(self, photo_bytes: bytes, caption: str = "", chat_id: Optional[str] = None) -> bool:
        """Send a photo/screenshot to Telegram."""
        target_chat = chat_id or self.chat_id
        if not self.token or not target_chat:
            return False

        client = self._http or httpx.AsyncClient(timeout=20.0)
        try:
            files = {"photo": ("screenshot.png", photo_bytes, "image/png")}
            data = {"chat_id": target_chat, "caption": caption[:1024]}
            res = await client.post(f"{self.api_url}/sendPhoto", data=data, files=files)
            return res.status_code == 200
        except Exception as exc:
            log.error("Telegram send_photo failed: %s", exc)
            return False
        finally:
            if not self._http:
                await client.aclose()

    # ------------------------------------------------------------------ pairing (review B2)
    def start_pairing(self) -> dict[str, Any]:
        raw = "".join(secrets.choice(PAIR_ALPHABET) for _ in range(6))
        self._pair_code, self._pair_expires, self._pair_tries = raw, self.clock() + PAIR_TTL_S, 0
        return {"code": raw, "expires_in": int(PAIR_TTL_S)}

    def pairing_pending(self) -> bool:
        return bool(self._pair_code) and self.clock() < self._pair_expires

    def unpair(self) -> None:
        self.chat_id = ""
        self._pair_code = None
        save_owner(self.owner_file, "")

    async def _try_pair(self, text: str, sender_chat_id: str) -> None:
        parts = text.split(None, 1)
        code = "".join(ch for ch in (parts[1] if len(parts) > 1 else "") if ch.isalnum()).upper()
        if not self.pairing_pending():
            self._pair_code = None
            await self.send_message("No pairing is waiting. Get a code in Orion first (Control > Pair).",
                                    chat_id=sender_chat_id, parse_mode="")
            return
        if code and hmac.compare_digest(code.encode(), (self._pair_code or "").encode()):
            self._pair_code = None
            self.chat_id = sender_chat_id
            save_owner(self.owner_file, sender_chat_id)
            log.info("Telegram paired with chat %s", sender_chat_id)
            await self.send_message("Paired. This chat can now talk to Orion.", chat_id=sender_chat_id, parse_mode="")
            return
        self._pair_tries += 1
        if self._pair_tries >= PAIR_MAX_TRIES:
            self._pair_code = None
        await self.send_message("That code didn't match.", chat_id=sender_chat_id, parse_mode="")

    async def handle_update(self, update: dict[str, Any]) -> None:
        """One incoming Telegram update: pairing, the paired owner's commands, or someone else's message."""
        msg = update.get("message") or {}
        from_user = msg.get("from", {}).get("username") or msg.get("from", {}).get("first_name", "User")
        text = (msg.get("text") or "").strip()
        sender_chat_id = str(msg.get("chat", {}).get("id", ""))
        if not text or not sender_chat_id:
            return
        if text.split(None, 1)[0].split("@", 1)[0].lower() == "/pair":
            await self._try_pair(text, sender_chat_id)
            return
        if self.chat_id and sender_chat_id == self.chat_id:
            if self.on_user_message:
                await self.on_user_message(text, sender_chat_id)
            return
        # Not the paired owner (or nobody paired yet): plain text for the inbox, never a command.
        if self.on_foreign_message:
            chat = msg.get("chat", {})
            title = chat.get("title") or " ".join(
                filter(None, [chat.get("first_name"), chat.get("last_name")])
            ) or chat.get("username") or sender_chat_id
            await self.on_foreign_message({
                "platform": "telegram",
                "thread_id": sender_chat_id,
                "thread_name": title,
                "author": from_user,
                "text": text,
                "route": "telegram_api",
                "timestamp": float(msg.get("date") or time.time()),
                "external_id": str(msg.get("message_id", "")) or None,
            })

    async def _poll_updates(self) -> None:
        """Long-polling task to receive incoming Telegram texts from the user."""
        log.info("Telegram long-polling started")
        while True:
            try:
                if not self._http:
                    break
                params = {"offset": self._last_update_id + 1, "timeout": 20}
                res = await self._http.get(f"{self.api_url}/getUpdates", params=params, timeout=25.0)
                if res.status_code == 200:
                    data = res.json()
                    updates = data.get("result", [])
                    for update in updates:
                        self._last_update_id = update["update_id"]
                        await self.handle_update(update)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                log.debug("Telegram polling transient error: %s", exc)
                await asyncio.sleep(4.0)


def make_router(get_daemon: Callable[[], Any]):
    """Pairing routes (review B2). The HUD and `orion-ctl telegram pair` show the code."""
    from fastapi import APIRouter
    from fastapi.responses import JSONResponse

    router = APIRouter()

    def bridge() -> Optional[TelegramBridge]:
        return getattr(get_daemon(), "telegram", None)

    @router.post("/telegram/pair")
    async def pair() -> Any:
        tg = bridge()
        if tg is None or not tg.token:
            return JSONResponse({"error": "Add your Telegram bot token first (TELEGRAM_BOT_TOKEN), then restart Orion."},
                                status_code=409)
        return {**tg.start_pairing(), "how": "Send /pair CODE to your Orion bot in Telegram."}

    @router.get("/telegram/pair")
    async def pair_state() -> Any:
        tg = bridge()
        return {"paired": bool(tg and tg.chat_id), "pending": bool(tg and tg.pairing_pending()),
                "running": bool(tg and tg.is_running)}

    @router.post("/telegram/unpair")
    async def unpair() -> Any:
        tg = bridge()
        if tg is not None:
            tg.unpair()
        return {"paired": False}

    return router
