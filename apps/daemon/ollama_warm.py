"""Keep the local model warm, so offline answers, indexing and quick completions don't wait for a cold load.

Every few minutes (ORION_OLLAMA_WARM_INTERVAL, default 240 s) Orion asks Ollama to keep the configured model
(OLLAMA_MODEL) loaded for a while longer: POST /api/generate with an empty prompt and keep_alive, which loads
the model if needed and generates nothing.

It holds back when:
  * it's turned off (ORION_OLLAMA_WARM=false);
  * the laptop is on battery and below 30 % (ORION_OLLAMA_WARM_MIN_BATTERY; Linux only — elsewhere there is
    no check);
  * Ollama isn't reachable — then it retries with backoff (30 s, 1 min, 2 min ... up to 15 min).
GET /ollama/warm shows the state.
"""
from __future__ import annotations

import asyncio
import logging
import sys
import time
from pathlib import Path
from typing import Any, Callable, Optional

import httpx
from fastapi import APIRouter

from config import _bool, _float, _int, _str, settings

log = logging.getLogger("orion.ollama.warm")

POWER_SUPPLY = Path("/sys/class/power_supply")
BACKOFF_START_S = 30.0
BACKOFF_MAX_S = 900.0


def read_battery(root: Path = POWER_SUPPLY, platform: Optional[str] = None) -> Optional[dict[str, Any]]:
    """{"on_battery": bool, "percent": int} on Linux laptops; None when there's no battery or no way to tell."""
    if not (platform or sys.platform).startswith("linux"):
        return None   # Windows/macOS: no check (see module docstring)
    try:
        supplies = [p for p in root.iterdir() if p.is_dir()]
    except OSError:
        return None

    def read(p: Path, name: str) -> str:
        try:
            return (p / name).read_text(encoding="utf-8").strip()
        except OSError:
            return ""

    batteries, mains_online = [], None
    for p in supplies:
        kind = read(p, "type")
        if kind == "Battery" and read(p, "scope") != "Device":   # skip mice / headsets
            cap = read(p, "capacity")
            if cap.isdigit():
                batteries.append((int(cap), read(p, "status")))
        elif kind in ("Mains", "USB", "USB_C", "USB_PD"):
            online = read(p, "online")
            if online in ("0", "1"):
                mains_online = bool(mains_online) or online == "1"
    if not batteries:
        return None
    percent = round(sum(c for c, _ in batteries) / len(batteries))
    if mains_online is not None:
        on_battery = not mains_online
    else:
        on_battery = any(s == "Discharging" for _, s in batteries)
    return {"on_battery": on_battery, "percent": percent}


class OllamaWarmer:
    def __init__(self, url: Optional[str] = None, model: Optional[str] = None, enabled: Optional[bool] = None,
                 interval_s: Optional[float] = None, keep_alive: Optional[str] = None,
                 min_battery: Optional[int] = None,
                 client_factory: Optional[Callable[[], httpx.AsyncClient]] = None,
                 battery: Optional[Callable[[], Optional[dict[str, Any]]]] = None,
                 clock: Callable[[], float] = time.time) -> None:
        self.url = (url or settings.ollama_url).rstrip("/")
        self.model = model or settings.ollama_model
        self.enabled = _bool("ORION_OLLAMA_WARM", True) if enabled is None else enabled
        self.interval_s = interval_s if interval_s is not None else max(30.0, _float("ORION_OLLAMA_WARM_INTERVAL", 240.0))
        self.keep_alive = keep_alive or _str("ORION_OLLAMA_KEEP_ALIVE", "15m")
        self.min_battery = min_battery if min_battery is not None else _int("ORION_OLLAMA_WARM_MIN_BATTERY", 30)
        self.client_factory = client_factory or (lambda: httpx.AsyncClient(timeout=httpx.Timeout(120.0, connect=3.0)))
        self.battery = battery or read_battery
        self.clock = clock
        self.failures = 0
        self.loaded: Optional[bool] = None
        self.last_ping: Optional[float] = None       # last attempt
        self.last_ok: Optional[float] = None         # last time the model was confirmed warm
        self.next_at: Optional[float] = None
        self.paused_reason: Optional[str] = None
        self.last_error: Optional[str] = None
        self.battery_state: Optional[dict[str, Any]] = None
        self._task: Optional[asyncio.Task] = None

    # ------------------------------------------------------------------ one step
    def backoff_s(self) -> float:
        if self.failures <= 0:
            return self.interval_s
        return min(BACKOFF_MAX_S, BACKOFF_START_S * 2 ** (self.failures - 1))

    def pause_reason(self) -> Optional[str]:
        if not self.enabled:
            return "Turned off in settings (ORION_OLLAMA_WARM)."
        if not self.model:
            return "No local model is set (OLLAMA_MODEL)."
        try:
            self.battery_state = self.battery()
        except Exception:
            self.battery_state = None
        b = self.battery_state
        if b and b.get("on_battery") and b.get("percent", 100) < self.min_battery:
            return f"On battery at {b['percent']}% (below {self.min_battery}%), saving power."
        return None

    async def tick(self) -> float:
        """Do one check/ping. Returns how long to wait before the next one."""
        self.paused_reason = self.pause_reason()
        if self.paused_reason:
            self.loaded = None if not self.enabled else self.loaded
            return self.interval_s
        self.last_ping = self.clock()
        try:
            async with self.client_factory() as client:
                tags = await client.get(f"{self.url}/api/tags", timeout=3.0)
                tags.raise_for_status()
                names = {m.get("name") for m in tags.json().get("models", [])} | \
                        {m.get("model") for m in tags.json().get("models", [])}
                if self.model not in names and f"{self.model}:latest" not in names:
                    self.failures = 0
                    self.loaded = False
                    self.paused_reason = f"The model {self.model} isn't downloaded yet (ollama pull {self.model})."
                    return self.interval_s
                res = await client.post(f"{self.url}/api/generate",
                                        json={"model": self.model, "prompt": "", "stream": False,
                                              "keep_alive": self.keep_alive})
                res.raise_for_status()
                self.loaded = await self._is_loaded(client)
        except (httpx.HTTPError, ValueError) as exc:
            self.failures += 1
            self.loaded = False
            self.last_error = "Ollama isn't reachable." if isinstance(exc, httpx.TransportError) else \
                f"Ollama answered with an error: {str(exc)[:160]}"
            return self.backoff_s()
        self.failures = 0
        self.last_error = None
        self.last_ok = self.clock()
        return self.interval_s

    async def _is_loaded(self, client: httpx.AsyncClient) -> bool:
        try:
            ps = await client.get(f"{self.url}/api/ps", timeout=3.0)
            ps.raise_for_status()
            models = ps.json().get("models", [])
        except (httpx.HTTPError, ValueError):
            return True   # older Ollama without /api/ps: the generate call succeeded, so it's loaded
        base = self.model if ":" in self.model else f"{self.model}:latest"
        return any(m.get("name") in (self.model, base) or m.get("model") in (self.model, base) for m in models)

    # ------------------------------------------------------------------ loop
    async def _loop(self) -> None:
        while True:
            try:
                wait = await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("warm-up check failed")
                wait = self.interval_s
            self.next_at = self.clock() + wait
            await asyncio.sleep(wait)

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.get_running_loop().create_task(self._loop())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
            self._task = None

    def status(self) -> dict[str, Any]:
        reason = self.paused_reason or self.last_error
        return {
            "model": self.model,
            "enabled": self.enabled,
            "loaded": self.loaded,
            "warming": self._task is not None and not self._task.done(),
            "paused": bool(self.paused_reason) or self.failures > 0,
            "reason": reason,
            "last_ping": self.last_ping,
            "last_ok": self.last_ok,
            "next_check": self.next_at,
            "failures": self.failures,
            "keep_alive": self.keep_alive,
            "interval_s": self.interval_s,
            "battery": self.battery_state,
        }


warmer = OllamaWarmer()
router = APIRouter()


@router.get("/ollama/warm")
async def ollama_warm_status() -> dict[str, Any]:
    return warmer.status()
