"""Central IPC telemetry bus: WebSocket fan-out to desktop HUD + extensions."""
from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Literal

from fastapi import WebSocket

log = logging.getLogger("orion.bus")

Audience = Literal["ui", "all"]


@dataclass
class Client:
    ws: WebSocket
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    kind: str = "unknown"  # desktop | extension | unknown
    browser: str | None = None
    connected_at: float = field(default_factory=time.time)

    def info(self) -> dict[str, Any]:
        return {"id": self.id, "kind": self.kind, "browser": self.browser, "connected_at": self.connected_at}


class EventBus:
    def __init__(self, history: int = 200) -> None:
        self.clients: dict[str, Client] = {}
        self.history: deque[dict[str, Any]] = deque(maxlen=history)

    async def register(self, ws: WebSocket) -> Client:
        client = Client(ws=ws)
        self.clients[client.id] = client
        log.info("Client %s connected (%d total)", client.id, len(self.clients))
        return client

    async def unregister(self, client: Client) -> None:
        self.clients.pop(client.id, None)
        log.info("Client %s (%s) disconnected", client.id, client.kind)

    def clients_state(self) -> list[dict[str, Any]]:
        return [c.info() for c in self.clients.values()]

    async def send(self, client: Client, event: dict[str, Any]) -> bool:
        event.setdefault("timestamp", time.time())
        try:
            await client.ws.send_text(json.dumps(event, default=str))
            return True
        except Exception:  # socket gone
            self.clients.pop(client.id, None)
            return False

    async def publish(self, event: dict[str, Any], *, audience: Audience = "ui", record: bool = True) -> None:
        """Broadcast an event. Extensions only receive audience='all' events."""
        event.setdefault("timestamp", time.time())
        if record:
            self.history.append(event)
        targets = [c for c in list(self.clients.values()) if audience == "all" or c.kind != "extension"]
        if targets:
            await asyncio.gather(*(self.send(c, event) for c in targets))

    async def log(self, message: str, level: str = "info") -> None:
        getattr(log, "warning" if level == "warn" else level, log.info)(message)
        await self.publish({"event": "LOG", "level": level, "message": message})
