"""Pairing the Orion Bridge browser extension (review item B1).

The extension can't read the daemon's token file, so it pairs once:

  1. In the HUD (Control drawer > Pair) you press "Pair browser extension". The HUD asks POST /extension/pair/start
     and shows a short code, good for 2 minutes and 5 tries.
  2. You type the code into the extension's popup. The extension sends it to POST /extension/pair (the only HTTP
     route a browser-extension origin may call besides /health) and gets back its own long key, which it keeps.
  3. From then on the extension opens the WebSocket with ?token=<its key>. That key only works from an extension
     origin, only on the WebSocket, and only as an "extension" client, which may never send privileged events
     (security.PRIVILEGED_EVENTS). It never works for HTTP.

Only a hash of each key is stored (data_dir/extension_keys.json). "Forget paired extensions" removes them all.

    POST /extension/pair/start   (token)          -> {"code", "expires_in"}
    POST /extension/pair         (extension)      {"code"} -> {"key"}
    GET  /extension/paired       (token)          -> {"paired": n, "pending": bool}
    POST /extension/unpair       (token)          -> {"paired": 0}
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import secrets
import threading
import time
from pathlib import Path
from typing import Any, Callable, Optional

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

log = logging.getLogger("orion.extension_pairing")

CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"   # no 0/O, 1/I
CODE_TTL_S = 120.0
MAX_TRIES = 5
MAX_KEYS = 20


def _hash(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def normalise_code(code: str) -> str:
    return "".join(ch for ch in str(code or "").upper() if ch.isalnum())


class ExtensionPairing:
    def __init__(self, data_dir: Path, clock: Callable[[], float] = time.time) -> None:
        self.path = Path(data_dir) / "extension_keys.json"
        self.clock = clock
        self.lock = threading.Lock()
        self._code: Optional[str] = None
        self._expires = 0.0
        self._tries = 0

    # ------------------------------------------------------------------ storage
    def _keys(self) -> list[dict[str, Any]]:
        try:
            d = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        keys = d.get("keys") if isinstance(d, dict) else None
        return [k for k in keys or [] if isinstance(k, dict) and isinstance(k.get("hash"), str)]

    def _save(self, keys: list[dict[str, Any]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump({"keys": keys[-MAX_KEYS:]}, f)
        os.replace(tmp, self.path)

    # ------------------------------------------------------------------ pairing
    def start(self) -> dict[str, Any]:
        with self.lock:
            raw = "".join(secrets.choice(CODE_ALPHABET) for _ in range(8))
            self._code, self._expires, self._tries = raw, self.clock() + CODE_TTL_S, 0
        return {"code": f"{raw[:4]}-{raw[4:]}", "expires_in": int(CODE_TTL_S)}

    def pending(self) -> bool:
        return bool(self._code) and self.clock() < self._expires

    def pair(self, code: str) -> Optional[str]:
        """The extension's new key, or None (wrong/expired code; the code dies after MAX_TRIES wrong tries)."""
        with self.lock:
            if not self._code or self.clock() >= self._expires:
                self._code = None
                return None
            if not hmac.compare_digest(normalise_code(code).encode(), self._code.encode()):
                self._tries += 1
                if self._tries >= MAX_TRIES:
                    self._code = None
                return None
            self._code = None
            key = secrets.token_urlsafe(32)
            keys = self._keys()
            keys.append({"hash": _hash(key), "at": self.clock()})
            self._save(keys)
        log.info("Browser extension paired")
        return key

    def check(self, key: Optional[str]) -> bool:
        if not key:
            return False
        h = _hash(key.strip())
        return any(hmac.compare_digest(h, k["hash"]) for k in self._keys())

    def count(self) -> int:
        return len(self._keys())

    def forget_all(self) -> None:
        with self.lock:
            self._save([])
            self._code = None


def make_router(get_pairing: Callable[[], ExtensionPairing]):
    router = APIRouter()

    @router.post("/extension/pair/start")
    async def pair_start() -> Any:
        return get_pairing().start()

    @router.post("/extension/pair")
    async def pair(request: Request) -> Any:
        try:
            body = await request.json()
        except ValueError:
            body = {}
        key = get_pairing().pair(str((body or {}).get("code") or ""))
        if not key:
            return JSONResponse({"error": "That code didn't work. Get a new one in Orion (Control > Pair)."},
                                status_code=403)
        return {"key": key}

    @router.get("/extension/paired")
    async def paired() -> Any:
        p = get_pairing()
        return {"paired": p.count(), "pending": p.pending()}

    @router.post("/extension/unpair")
    async def unpair() -> Any:
        get_pairing().forget_all()
        return {"paired": 0}

    return router
