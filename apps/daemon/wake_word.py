"""Wake word "Orion" with openWakeWord (OrionOS 1.0 plan, C. "Wake word 'Orion' + speech-to-text").

Off by default. When it's on (`orion-ctl mic wake on`, or `POST /wake {"on": true}`), the
hot mic (hotmic.py) runs every 30 ms of sound through a small openWakeWord model on this computer first. Only after
it hears "Orion" does the rest run: the voice-profile check (only your voice) and Whisper for the words. That saves
running Whisper on every sentence in the room, and it means "Orion" must really be said, not just transcribed.

If openWakeWord or the "orion" model isn't installed, the hot mic falls back to the old check (Whisper's text must
start with "Orion"), and GET /hotmic says why in `wake.why`.

The model: openWakeWord ships no "orion" model, so it's a custom one (trained with openWakeWord's own notebook, a
few minutes on a GPU), put at ~/.orion/models/wake/orion.onnx (or .tflite), or named by ORION_WAKE_MODEL.

Wyoming (optional, off by default): `wyoming_port` in hotmic.json starts a tiny Wyoming wake service on 127.0.0.1
only (Home Assistant's voice protocol: JSON lines with raw PCM payloads), so other local voice tools can use the
same detector. It never listens on the network.

Everything is visible: the hot mic light shows "Listening for “Orion”", and GET /privacy/dashboard lists it with
a one-tap off. The protected rules stay: voice never approves anything.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Any, Callable, Optional

import numpy as np
from pydantic import BaseModel

log = logging.getLogger("orion.wake")

CHUNK = 1280               # openWakeWord wants 80 ms at 16 kHz
RATE = 16000
ARMED_S = 6.0              # how long after "Orion" a phrase still counts
DEFAULT_THRESHOLD = 0.5
WYOMING_HOST = "127.0.0.1"


def find_model(data_dir: Path, environ: Optional[dict[str, str]] = None) -> Optional[Path]:
    env = os.environ if environ is None else environ
    named = env.get("ORION_WAKE_MODEL", "").strip()
    if named:
        p = Path(named).expanduser()
        return p if p.is_file() else None
    for ext in ("onnx", "tflite"):
        p = Path(data_dir) / "models" / "wake" / f"orion.{ext}"
        if p.is_file():
            return p
    return None


def why_not(data_dir: Path) -> str:
    """'' when the wake word can run; otherwise what's missing, in plain words."""
    try:
        import openwakeword  # noqa: F401  (voice add-on)
    except Exception:
        return ("the wake word add-on (openWakeWord) isn't installed: run "
                + ("orion-ctl setup voice" if sys.platform.startswith("linux")
                   else "pip install openwakeword in Orion's Python"))
    if find_model(data_dir) is None:
        return "the “orion” wake model isn't in ~/.orion/models/wake yet"
    return ""


class OpenWakeWordDetector:
    """predict(int16[1280]) -> score 0..1 for "orion"."""

    def __init__(self, model_path: Path) -> None:
        from openwakeword.model import Model  # voice add-on, guarded by why_not()
        fw = "tflite" if str(model_path).endswith(".tflite") else "onnx"
        self.model = Model(wakeword_models=[str(model_path)], inference_framework=fw)
        self.name = Path(model_path).stem

    def __call__(self, chunk: np.ndarray) -> float:
        scores = self.model.predict(chunk.astype(np.int16))
        return float(max(scores.values())) if scores else 0.0

    def reset(self) -> None:
        try:
            self.model.reset()
        except Exception:
            pass


class WakeGate:
    """Feeds 30 ms frames to a detector in 80 ms chunks; `armed()` is True for a few seconds after "Orion"."""

    def __init__(self, detect: Callable[[np.ndarray], float], threshold: float = DEFAULT_THRESHOLD,
                 armed_s: float = ARMED_S, clock: Callable[[], float] = time.monotonic,
                 on_wake: Callable[[float], Any] = lambda s: None) -> None:
        self.detect, self.threshold, self.armed_s, self.clock, self.on_wake = detect, threshold, armed_s, clock, on_wake
        self.buf = np.zeros(0, np.int16)
        self.until = 0.0
        self.last_score = 0.0

    def feed(self, frame: np.ndarray) -> bool:
        self.buf = np.concatenate([self.buf, frame.astype(np.int16)])
        fired = False
        while len(self.buf) >= CHUNK:
            chunk, self.buf = self.buf[:CHUNK], self.buf[CHUNK:]
            self.last_score = s = self.detect(chunk)
            if s >= self.threshold:
                if not self.armed():
                    self.on_wake(s)
                self.until = self.clock() + self.armed_s
                fired = True
        return fired

    def armed(self) -> bool:
        return self.clock() < self.until

    def disarm(self) -> None:
        self.until = 0.0


def make_gate(data_dir: Path, cfg: dict[str, Any], detector: Optional[Callable[[np.ndarray], float]] = None,
              on_wake: Callable[[float], Any] = lambda s: None) -> tuple[Optional[WakeGate], str]:
    """(gate, why). gate is None when the wake word is off or can't run (why says which)."""
    if not cfg.get("wake_word"):
        return None, "off"
    if detector is None:
        why = why_not(data_dir)
        if why:
            return None, why
        try:
            detector = OpenWakeWordDetector(find_model(data_dir))  # type: ignore[arg-type]
        except Exception as exc:
            log.warning("wake word model failed to load: %s", exc)
            return None, "the wake model didn't load"
    return WakeGate(detector, float(cfg.get("wake_threshold") or DEFAULT_THRESHOLD), on_wake=on_wake), ""


# ---------------------------------------------------------------------- Wyoming wake service (local only)
def encode_event(etype: str, data: Optional[dict[str, Any]] = None, payload: bytes = b"") -> bytes:
    body = json.dumps(data or {}).encode()
    head = {"type": etype, "data_length": len(body)}
    if payload:
        head["payload_length"] = len(payload)
    return json.dumps(head).encode() + b"\n" + body + payload


async def read_event(reader: asyncio.StreamReader) -> Optional[tuple[str, dict[str, Any], bytes]]:
    line = await reader.readline()
    if not line:
        return None
    head = json.loads(line)
    data = head.get("data") or {}
    if head.get("data_length"):
        data = {**data, **json.loads(await reader.readexactly(int(head["data_length"])))}
    payload = await reader.readexactly(int(head["payload_length"])) if head.get("payload_length") else b""
    return str(head.get("type", "")), data, payload


class WyomingWakeServer:
    """A Wyoming wake-word service on 127.0.0.1. `detector_factory()` gives a fresh detector per connection."""

    def __init__(self, detector_factory: Callable[[], Callable[[np.ndarray], float]], port: int = 10400,
                 threshold: float = DEFAULT_THRESHOLD, name: str = "orion") -> None:
        self.factory, self.port, self.threshold, self.name = detector_factory, port, threshold, name
        self.server: Optional[asyncio.base_events.Server] = None

    @property
    def running(self) -> bool:
        return self.server is not None

    async def start(self) -> int:
        self.server = await asyncio.start_server(self._client, WYOMING_HOST, self.port)
        self.port = self.server.sockets[0].getsockname()[1]
        log.info("Wyoming wake service on %s:%d", WYOMING_HOST, self.port)
        return self.port

    async def stop(self) -> None:
        if self.server is not None:
            self.server.close()
            await self.server.wait_closed()
            self.server = None

    def info(self) -> dict[str, Any]:
        return {"wake": [{"name": "openwakeword", "installed": True, "description": "Orion wake word",
                          "attribution": {"name": "openWakeWord", "url": "https://github.com/dscripka/openWakeWord"},
                          "models": [{"name": self.name, "phrase": "Orion", "installed": True, "languages": ["en"],
                                      "description": "Orion", "attribution": {"name": "Orion", "url": ""}}]}]}

    async def _client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        gate: Optional[WakeGate] = None
        detected = False
        try:
            while (ev := await read_event(reader)) is not None:
                etype, data, payload = ev
                if etype == "describe":
                    writer.write(encode_event("info", self.info()))
                elif etype == "audio-start":
                    gate, detected = WakeGate(self.factory(), self.threshold), False
                elif etype == "audio-chunk" and gate is not None:
                    if int(data.get("rate", RATE)) != RATE or int(data.get("width", 2)) != 2:
                        continue     # Orion's detector takes 16 kHz 16-bit mono only
                    if gate.feed(np.frombuffer(payload, dtype=np.int16)) and not detected:
                        detected = True
                        writer.write(encode_event("detection", {"name": self.name,
                                                                "timestamp": data.get("timestamp")}))
                elif etype == "audio-stop":
                    if not detected:
                        writer.write(encode_event("not-detected", {}))
                    gate = None
                await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionError, ValueError):
            pass
        finally:
            writer.close()


# ---------------------------------------------------------------------- daemon routes
async def apply(daemon: Any, restart_mic: bool = True) -> dict[str, Any]:
    """Bring the running hot mic and the Wyoming service in line with hotmic.json."""
    hm = daemon.hotmic
    cfg = hm.config()
    if restart_mic and hm.listening:     # the gate is built when listening starts: restart to pick up the change
        await asyncio.to_thread(hm.stop, False)
        await asyncio.to_thread(hm.start)
    wy: Optional[WyomingWakeServer] = getattr(daemon, "wyoming", None)
    port = int(cfg.get("wyoming_port") or 0)
    want = bool(cfg.get("wake_word")) and port > 0 and why_not(hm.data_dir) == ""
    if wy is not None and wy.running and not want:
        await wy.stop()
    elif want and not (wy is not None and wy.running):
        model = find_model(hm.data_dir)
        wy = WyomingWakeServer(lambda: OpenWakeWordDetector(model), port=port,  # type: ignore[arg-type]
                               threshold=float(cfg.get("wake_threshold") or DEFAULT_THRESHOLD))
        try:
            await wy.start()
        except OSError as exc:
            log.warning("Wyoming wake service didn't start: %s", exc)
        daemon.wyoming = wy
    return state(daemon)


def state(daemon: Any) -> dict[str, Any]:
    hm = daemon.hotmic
    cfg = hm.config()
    wy = getattr(daemon, "wyoming", None)
    why = why_not(hm.data_dir)   # [stability] say what's missing even while it's off, so turning it on isn't a surprise
    return {"on": bool(cfg.get("wake_word")), "ready": why == "", "why": why,
            "listening": bool(hm.listening), "threshold": cfg.get("wake_threshold"),
            "wyoming": {"running": bool(wy is not None and wy.running),
                        "port": wy.port if wy is not None and wy.running else None}}


class WakeReq(BaseModel):
    on: Optional[bool] = None
    threshold: Optional[float] = None
    wyoming_port: Optional[int] = None


def make_router(get_daemon: Callable[[], Any]):
    from fastapi import APIRouter

    router = APIRouter()

    @router.get("/wake")
    async def get_wake() -> dict[str, Any]:
        return state(get_daemon())

    @router.post("/wake")
    async def set_wake(req: WakeReq) -> dict[str, Any]:
        d = get_daemon()
        kv: dict[str, Any] = {}
        if req.on is not None:
            kv["wake_word"] = bool(req.on)
        if req.threshold is not None:
            kv["wake_threshold"] = min(0.95, max(0.2, float(req.threshold)))
        if req.wyoming_port is not None:
            kv["wyoming_port"] = req.wyoming_port if 1024 <= req.wyoming_port <= 65535 else 0
        d.hotmic.set_config(**kv)
        return await apply(d)

    return router
