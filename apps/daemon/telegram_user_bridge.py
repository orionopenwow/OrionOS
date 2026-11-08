"""Optional Telethon user-account bridge for true personal Telegram DMs (V2 Comms hub).

The Bot API can only see chats that message the bot. To mirror your real one-to-one
Telegram conversations into Orion's Comms tab, set TELEGRAM_API_ID / TELEGRAM_API_HASH
(from https://my.telegram.org -> API development tools), install the optional extras
(`npm run setup:daemon:full`) and log in once:

    cd apps/daemon && .venv\\Scripts\\python telegram_login.py

The session file lives at ~/.orion/telegram_user.session. Only private chats are mirrored;
groups and channels are ignored.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Awaitable, Callable

log = logging.getLogger("orion.telegram_user")

OnMessage = Callable[[dict[str, Any]], Awaitable[None]]


def session_path(data_dir: Path) -> Path:
    return data_dir / "telegram_user"


class TelegramUserBridge:
    def __init__(self, api_id: int, api_hash: str, data_dir: Path, on_message: OnMessage) -> None:
        self.api_id = api_id
        self.api_hash = api_hash
        self.session = session_path(data_dir)
        self.on_message = on_message
        self._client: Any = None
        self.active = False

    async def start(self) -> bool:
        if not (self.api_id and self.api_hash):
            return False
        try:
            from telethon import TelegramClient, events
        except ImportError:
            log.warning("TELEGRAM_API_ID set but telethon is not installed (npm run setup:daemon:full)")
            return False

        client = TelegramClient(str(self.session), self.api_id, self.api_hash)
        await client.connect()
        if not await client.is_user_authorized():
            log.warning("Telegram user session not authorised. Run `python telegram_login.py` once in apps/daemon.")
            await client.disconnect()
            return False

        me = await client.get_me()
        bridge = self

        @client.on(events.NewMessage())
        async def _handler(event: Any) -> None:
            if not event.is_private or not event.raw_text:
                return
            try:
                chat = await event.get_chat()
                name = " ".join(filter(None, [getattr(chat, "first_name", None), getattr(chat, "last_name", None)])) \
                    or getattr(chat, "username", None) or str(event.chat_id)
                if getattr(chat, "bot", False):
                    return
                if event.out:
                    author = "You"
                else:
                    sender = await event.get_sender()
                    author = getattr(sender, "first_name", None) or getattr(sender, "username", None) or name
                await bridge.on_message({
                    "platform": "telegram",
                    "thread_id": str(event.chat_id),
                    "thread_name": name,
                    "author": author,
                    "text": event.raw_text,
                    "route": "telegram_user",
                    "direction": "out" if event.out else "in",
                    "timestamp": event.date.timestamp() if event.date else None,
                    "external_id": str(event.id),
                })
            except Exception:
                log.exception("Telegram user message handling failed")

        self._client = client
        self.active = True
        log.info("Telegram user bridge online as %s", getattr(me, "username", None) or getattr(me, "first_name", "me"))
        return True

    async def send(self, thread: dict[str, Any], text: str) -> bool:
        if not self._client:
            return False
        await self._client.send_message(int(thread["thread_id"]), text)
        return True

    async def stop(self) -> None:
        if self._client is not None:
            await self._client.disconnect()
        self._client = None
        self.active = False


def _interactive_login() -> None:
    import asyncio

    from config import settings

    async def _run() -> None:
        from telethon import TelegramClient

        if not (settings.telegram_api_id and settings.telegram_api_hash):
            raise SystemExit("Set TELEGRAM_API_ID and TELEGRAM_API_HASH in apps/daemon/.env first.")
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        client = TelegramClient(str(session_path(settings.data_dir)), settings.telegram_api_id,
                                settings.telegram_api_hash)
        await client.start()  # prompts for phone, code and 2FA password in the terminal
        me = await client.get_me()
        print(f"Logged in as {getattr(me, 'username', None) or me.first_name}. Session saved; restart the daemon.")
        await client.disconnect()

    asyncio.run(_run())


if __name__ == "__main__":
    _interactive_login()

