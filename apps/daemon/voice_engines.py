"""Orion's voice engines: which voice Orion speaks with, and the routes the voice set-up step uses.

Engines
  kokoro    built in (Kokoro 82M, Apache-2.0). The default; behaviour is unchanged when it is picked.
  voicebox  the Voicebox app (MIT, https://github.com/jamiepine/voicebox) when it runs on this computer. Orion calls
            its local API on 127.0.0.1:17493 (POST /v1/audio/speech returns a WAV). If Voicebox isn't running,
            Orion falls back to Kokoro and says so in plain words.
  piper     only if a `piper` program and a voice file (~/.orion/models/piper/*.onnx) are already on this computer.

Rules kept here
  - Local only: the Voicebox address must be 127.0.0.1, localhost or ::1 (anything else is refused), proxies from
    the environment are ignored and redirects are not followed. No audio is ever sent anywhere else.
  - Voice cloning: Orion never makes a cloned voice. It can design one from a written description (Voicebox
    VoiceDesign) or use a preset. Picking a cloned Voicebox voice needs the consent box ticked
    ("Only use your own voice or a voice you have permission to use").

Settings live in <data dir>/voice/settings.json: engine, voice, speed, warmth, delivery, chosen.
Routes: GET /voice/engines, POST /voice/set, POST /voice/preview, POST /voice/design, POST /voice/hear.
"""
from __future__ import annotations

import asyncio
import base64
import importlib.util
import io
import json
import logging
import os
import shutil
import subprocess
import time
import uuid
import wave
from pathlib import Path
from typing import Any, Callable, Optional
from urllib.parse import urlsplit

import numpy as np
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

log = logging.getLogger("orion.voice_engines")

SAMPLE_RATE = 24000                       # what the HUD's waveform and Kokoro use
VOICEBOX_URL = "http://127.0.0.1:17493"   # Voicebox's own default port
LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}
ENGINES = ("kokoro", "voicebox", "piper")
CONSENT_LINE = "Only use your own voice or a voice you have permission to use."
SAMPLE_LINE = "Morning. I'm Orion. This is how I'll sound when we talk."
DEFAULTS: dict[str, Any] = {
    "engine": "kokoro", "voice": "af_nicole", "speed": 0.92, "warmth": 0.0, "delivery": "", "chosen": False,
}

# Kokoro voices that ship with the Kokoro model Orion already downloads (American English, lang_code "a").
KOKORO_VOICES: list[dict[str, str]] = [
    {"id": "af_nicole", "name": "Nicole and Heart (Orion's usual voice)", "gender": "female"},
    {"id": "af_heart", "name": "Heart", "gender": "female"},
    {"id": "af_bella", "name": "Bella", "gender": "female"},
    {"id": "af_sky", "name": "Sky", "gender": "female"},
    {"id": "am_michael", "name": "Michael", "gender": "male"},
    {"id": "am_adam", "name": "Adam", "gender": "male"},
]
KOKORO_IDS = {v["id"] for v in KOKORO_VOICES} | {"blend", "default", "calm_empathetic"}

# Voicebox engines that take a "delivery" instruction (Qwen CustomVoice / VoiceDesign / Qwen TTS).
INSTRUCT_ENGINES = {"qwen", "qwen_custom_voice", "qwen_voice_design"}


class LocalOnlyError(ValueError):
    """The Voicebox address isn't on this computer."""


def local_url(url: str) -> str:
    """Return the URL without a trailing slash if it points at this computer over http; refuse anything else."""
    parts = urlsplit(url.strip())
    if parts.scheme != "http" or (parts.hostname or "") not in LOCAL_HOSTS or parts.username or parts.password:
        raise LocalOnlyError("Orion only talks to Voicebox on this computer (127.0.0.1).")
    return url.strip().rstrip("/")


# ---------------------------------------------------------------- audio helpers (pure numpy + stdlib)
def wav_to_samples(data: bytes) -> tuple[np.ndarray, int]:
    """Decode a PCM WAV (8/16/32-bit, mono or stereo) into float32 mono samples and its sample rate."""
    with wave.open(io.BytesIO(data), "rb") as w:
        rate, width, channels = w.getframerate(), w.getsampwidth(), w.getnchannels()
        raw = w.readframes(w.getnframes())
    if width == 2:
        arr = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
    elif width == 4:
        arr = np.frombuffer(raw, dtype="<i4").astype(np.float32) / 2147483648.0
    elif width == 1:
        arr = (np.frombuffer(raw, dtype=np.uint8).astype(np.float32) - 128.0) / 128.0
    else:
        raise ValueError(f"unsupported WAV sample width {width}")
    if channels > 1:
        arr = arr.reshape(-1, channels).mean(axis=1)
    return arr.astype(np.float32), rate


def samples_to_wav(samples: np.ndarray, rate: int = SAMPLE_RATE) -> bytes:
    pcm = (np.clip(samples, -1.0, 1.0) * 32767).astype("<i2")
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm.tobytes())
    return buf.getvalue()


def resample(samples: np.ndarray, src: int, dst: int = SAMPLE_RATE) -> np.ndarray:
    if src == dst or samples.size == 0:
        return samples.astype(np.float32)
    n = max(1, int(round(samples.size * dst / src)))
    x_old = np.linspace(0.0, 1.0, samples.size, endpoint=False)
    x_new = np.linspace(0.0, 1.0, n, endpoint=False)
    return np.interp(x_new, x_old, samples).astype(np.float32)


def warm(samples: np.ndarray, warmth: float, rate: int = SAMPLE_RATE) -> np.ndarray:
    """Warmth 0..1: gently softens the bright top end (a one-pole low-pass mixed in). 0 leaves the audio untouched."""
    w = min(1.0, max(0.0, float(warmth)))
    if w <= 0.0 or samples.size == 0:
        return samples
    cutoff = 5200.0 - 2600.0 * w                    # 5.2 kHz at a touch of warmth, 2.6 kHz at full
    a = float(np.exp(-2.0 * np.pi * cutoff / rate))
    low = np.empty_like(samples)
    acc = 0.0
    for i, s in enumerate(samples):                 # short sentences: fine in a worker thread
        acc = (1.0 - a) * float(s) + a * acc
        low[i] = acc
    mix = 0.6 * w
    out = (1.0 - mix) * samples + mix * low
    peak = float(np.max(np.abs(out))) or 1.0
    return (out / max(1.0, peak)).astype(np.float32)


# ---------------------------------------------------------------- Voicebox (local REST API)
class Voicebox:
    """A small client for the Voicebox API on this computer (shapes from its backend/routes)."""

    def __init__(self, url: Optional[str] = None, timeout: float = 120.0) -> None:
        self.url = local_url(url or os.getenv("ORION_VOICEBOX_URL") or VOICEBOX_URL)
        self.timeout = timeout

    def _client(self, timeout: Optional[float] = None):
        import httpx
        # trust_env=False: never route through a proxy from the environment; redirects are not followed.
        return httpx.AsyncClient(base_url=self.url, timeout=timeout or self.timeout, trust_env=False,
                                 follow_redirects=False)

    async def health(self) -> Optional[dict[str, Any]]:
        """GET /health -> {"status": "healthy", "gpu_available", "gpu_type", ...} or None when not running."""
        try:
            async with self._client(2.0) as c:
                r = await c.get("/health")
            if r.status_code == 200:
                j = r.json()
                return j if isinstance(j, dict) else {"status": "healthy"}
        except Exception as exc:
            log.debug("Voicebox health: %s", exc)
        return None

    async def profiles(self) -> list[dict[str, Any]]:
        """GET /profiles -> [{id, name, voice_type: cloned|preset|designed, default_engine, language, ...}]."""
        async with self._client(5.0) as c:
            r = await c.get("/profiles")
        r.raise_for_status()
        j = r.json()
        return j if isinstance(j, list) else []

    async def presets(self, engine: str) -> list[dict[str, Any]]:
        """GET /profiles/presets/{engine} -> {"engine", "voices": [{voice_id, name, gender, language}]}."""
        async with self._client(5.0) as c:
            r = await c.get(f"/profiles/presets/{engine}")
        r.raise_for_status()
        j = r.json()
        return list(j.get("voices") or []) if isinstance(j, dict) else []

    async def create_profile(self, body: dict[str, Any]) -> dict[str, Any]:
        """POST /profiles (VoiceProfileCreate: name, description, language, voice_type, preset_engine,
        preset_voice_id, design_prompt, default_engine)."""
        async with self._client(15.0) as c:
            r = await c.post("/profiles", json=body)
        if r.status_code >= 400:
            raise RuntimeError(_error_text(r))
        return r.json()

    async def speech(self, text: str, profile: str, speed: float = 1.0, instructions: str = "") -> bytes:
        """POST /v1/audio/speech (OpenAI-compatible) -> WAV bytes. "tts-1" means: the profile's own engine."""
        body: dict[str, Any] = {"model": "tts-1", "input": text[:10000], "voice": profile,
                                "response_format": "wav", "speed": min(4.0, max(0.25, float(speed)))}
        if instructions.strip():
            body["instructions"] = instructions.strip()[:500]
        async with self._client() as c:
            r = await c.post("/v1/audio/speech", json=body)
        if r.status_code >= 400:
            raise RuntimeError(_error_text(r))
        return r.content


def _error_text(r: Any) -> str:
    try:
        j = r.json()
        if isinstance(j, dict):
            e = j.get("error")
            if isinstance(e, dict) and e.get("message"):
                return str(e["message"])
            if j.get("detail"):
                return str(j["detail"])
    except Exception:
        pass
    return f"Voicebox answered {r.status_code}."


# ---------------------------------------------------------------- Piper (only when already installed)
def piper_models(models_dir: Optional[Path] = None) -> list[Path]:
    d = models_dir or Path(os.path.expanduser(os.getenv("ORION_PIPER_DIR", "~/.orion/models/piper")))
    try:
        return sorted(d.glob("*.onnx"))
    except OSError:
        return []


def piper_available() -> bool:
    return bool(shutil.which("piper")) and bool(piper_models())


def piper_synth(text: str, model: Path, speed: float) -> tuple[np.ndarray, int]:
    exe = shutil.which("piper")
    if not exe:
        raise RuntimeError("Piper isn't installed.")
    length = f"{1.0 / max(0.5, min(1.5, speed)):.2f}"   # piper: bigger length scale = slower
    out = subprocess.run([exe, "--model", str(model), "--length_scale", length, "--output_file", "-"],
                         input=text.encode(), capture_output=True, timeout=60, check=False)
    if out.returncode != 0 or not out.stdout:
        raise RuntimeError("Piper couldn't speak that.")
    return wav_to_samples(out.stdout)


# ---------------------------------------------------------------- settings + engine choice
class VoiceEngines:
    def __init__(self, data_dir: Path, voicebox: Optional[Voicebox] = None) -> None:
        self.path = Path(data_dir) / "voice" / "settings.json"
        self._voicebox = voicebox
        self.note = ""
        self._vb_cache: tuple[float, dict[str, Any]] = (0.0, {})

    # --- settings
    def settings(self) -> dict[str, Any]:
        s = dict(DEFAULTS)
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                s.update({k: raw[k] for k in DEFAULTS if k in raw})
        except (OSError, ValueError):
            pass
        return s

    def _save(self, s: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps({k: s[k] for k in DEFAULTS}, indent=2), encoding="utf-8")
        tmp.replace(self.path)

    @property
    def voicebox(self) -> Voicebox:
        if self._voicebox is None:
            self._voicebox = Voicebox()
        return self._voicebox

    # --- what's available
    @staticmethod
    def kokoro_available() -> bool:
        try:
            return importlib.util.find_spec("kokoro") is not None
        except (ImportError, ValueError):
            return False

    async def voicebox_state(self, fresh: bool = False) -> dict[str, Any]:
        """Is Voicebox running here, its GPU, its voices (profiles) and the presets Orion can turn into voices."""
        at, cached = self._vb_cache
        if not fresh and cached and time.monotonic() - at < 10:
            return cached
        try:
            vb = self.voicebox
        except LocalOnlyError as exc:
            st = {"reachable": False, "url": "", "voices": [], "error": str(exc)}
            self._vb_cache = (time.monotonic(), st)
            return st
        health = await vb.health()
        st: dict[str, Any] = {"reachable": health is not None, "url": vb.url, "voices": []}
        if health is not None:
            st["gpu"] = health.get("gpu_type") if health.get("gpu_available") else None
            voices: list[dict[str, Any]] = []
            try:
                for p in await vb.profiles():
                    voices.append({"id": str(p.get("id")), "name": str(p.get("name") or p.get("id")),
                                   "kind": str(p.get("voice_type") or "cloned"),
                                   "engine": p.get("default_engine") or p.get("preset_engine") or "",
                                   "language": p.get("language") or ""})
            except Exception as exc:
                log.info("Voicebox profiles: %s", exc)
            have = {(v["engine"], v["name"]) for v in voices}
            try:
                for p in await vb.presets("qwen_custom_voice"):
                    name = f"{p.get('name')} (Qwen preset)"
                    vid = str(p.get("voice_id"))
                    if ("qwen_custom_voice", f"Orion: {vid.replace('_', ' ')}") in have:
                        continue
                    voices.append({"id": f"preset:qwen_custom_voice:{vid}", "name": name, "kind": "preset",
                                   "engine": "qwen_custom_voice", "language": p.get("language") or "",
                                   "gender": p.get("gender") or ""})
            except Exception as exc:
                log.info("Voicebox presets: %s", exc)
            st["voices"] = voices
        self._vb_cache = (time.monotonic(), st)
        return st

    async def engines(self) -> dict[str, Any]:
        s = self.settings()
        vb = await self.voicebox_state()
        piper = piper_models() if shutil.which("piper") else []
        return {
            "active": s,
            "chosen": bool(s.get("chosen")),
            "engines": [
                {"id": "kokoro", "name": "Kokoro (built in)", "available": self.kokoro_available(),
                 "voices": KOKORO_VOICES, "speed": True, "warmth": True, "delivery": False, "design": False,
                 "about": "Clear and quick on any computer. Apache-2.0."},
                {"id": "voicebox", "name": "Voicebox", "available": vb["reachable"], "voices": vb["voices"],
                 "speed": True, "warmth": True, "delivery": True, "design": vb["reachable"],
                 "about": "More natural voices, voices from a description, and delivery styles. "
                          "Best with a graphics card. MIT.",
                 "gpu": vb.get("gpu")},
                {"id": "piper", "name": "Piper (tiny)", "available": bool(piper),
                 "voices": [{"id": p.stem, "name": p.stem} for p in piper], "speed": True, "warmth": True,
                 "delivery": False, "design": False, "about": "Very small and fast. Each voice has its own licence."},
            ],
            "voicebox": {"reachable": vb["reachable"], "url": vb.get("url", ""), "gpu": vb.get("gpu"),
                         "error": vb.get("error", ""),
                         "how": "" if vb["reachable"] else
                         "Voicebox isn't running. Start it, or install it (OrionOS: Settings > Orion's voice). "
                         "Orion uses its built-in voice until then."},
            "note": self.note,
            "consent": CONSENT_LINE,
            "sample": SAMPLE_LINE,
        }

    # --- choosing
    async def set(self, body: dict[str, Any]) -> dict[str, Any]:
        s = self.settings()
        engine = str(body.get("engine") or s["engine"])
        if engine not in ENGINES:
            raise ValueError("Pick kokoro, voicebox or piper.")
        voice = str(body.get("voice") if body.get("voice") is not None else s["voice"]).strip()
        if engine != s["engine"] and body.get("voice") is None:
            voice = DEFAULTS["voice"] if engine == "kokoro" else ""
        if engine == "kokoro" and voice not in KOKORO_IDS:
            voice = DEFAULTS["voice"]
        if engine == "voicebox" and voice:
            voice = await self._voicebox_voice(voice, bool(body.get("consent")))
        if engine == "piper" and voice and voice not in {p.stem for p in piper_models()}:
            raise ValueError("That Piper voice isn't on this computer.")
        s.update(engine=engine, voice=voice)
        if "speed" in body:
            s["speed"] = round(min(1.5, max(0.5, float(body["speed"]))), 2)
        if "warmth" in body:
            s["warmth"] = round(min(1.0, max(0.0, float(body["warmth"]))), 2)
        if "delivery" in body:
            s["delivery"] = str(body.get("delivery") or "").strip()[:300]
        if body.get("chosen") is not None:
            s["chosen"] = bool(body["chosen"])
        self._save(s)
        return {"ok": True, "active": s, "message": "Saved. Orion will use this voice from now on."}

    async def _voicebox_voice(self, voice: str, consent: bool) -> str:
        """A Voicebox profile id. A preset ("preset:<engine>:<id>") becomes a profile the first time it's picked."""
        st = await self.voicebox_state(fresh=True)
        if not st["reachable"]:
            raise RuntimeError("Voicebox isn't running, so its voices can't be picked right now.")
        if voice.startswith("preset:"):
            _, engine, vid = (voice.split(":", 2) + ["", ""])[:3]
            if engine not in ("qwen_custom_voice", "kokoro") or not vid:
                raise ValueError("That isn't a Voicebox preset.")
            name = f"Orion: {vid.replace('_', ' ')}"[:100]
            mine = next((v for v in st["voices"] if v.get("kind") == "preset" and v["name"] == name), None)
            if mine is not None:                       # made earlier: reuse it, never a second copy
                return str(mine["id"])
            prof = await self.voicebox.create_profile({
                "name": name, "language": "en", "voice_type": "preset",
                "preset_engine": engine, "preset_voice_id": vid,
                "description": "Made by Orion's voice set-up from a Voicebox preset."})
            self._vb_cache = (0.0, {})
            return str(prof.get("id"))
        found = next((v for v in st["voices"] if v["id"] == voice), None)
        if found is None:
            raise ValueError("Voicebox doesn't have that voice.")
        if found.get("kind") == "cloned" and not consent:
            raise PermissionError(f"This is a cloned voice. Tick the box first: {CONSENT_LINE}")
        return voice

    async def design(self, body: dict[str, Any]) -> dict[str, Any]:
        """Voicebox VoiceDesign: a new voice from a written description (no recording, no cloning)."""
        desc = str(body.get("description") or "").strip()
        if len(desc) < 8:
            raise ValueError("Describe the voice in a few words, for example: a calm, warm, low voice.")
        st = await self.voicebox_state(fresh=True)
        if not st["reachable"]:
            raise RuntimeError("Designing a voice needs Voicebox running on this computer.")
        name = str(body.get("name") or "").strip() or f"Orion designed {time.strftime('%d %b %H:%M')}"
        prof = await self.voicebox.create_profile({
            "name": name[:100], "description": desc[:500], "language": "en", "voice_type": "designed",
            "design_prompt": desc[:2000], "default_engine": "qwen_voice_design"})
        self._vb_cache = (0.0, {})
        out: dict[str, Any] = {"ok": True, "voice": {"id": str(prof.get("id")), "name": prof.get("name", name),
                                                     "kind": "designed", "engine": "qwen_voice_design"},
                               "message": "Made a new voice from your description. Tap Preview to hear it."}
        if body.get("use", True):
            out.update(await self.set({"engine": "voicebox", "voice": str(prof.get("id"))}))
            out["message"] = "Made a new voice from your description and picked it."
        return out

    # --- speaking
    async def synthesize(self, daemon: Any, text: str, over: Optional[dict[str, Any]] = None
                         ) -> tuple[np.ndarray, int, str]:
        """Speak `text` with the chosen (or `over`ridden) settings -> (samples at 24 kHz, rate, engine used)."""
        s = {**self.settings(), **{k: v for k, v in (over or {}).items() if k in DEFAULTS and v is not None}}
        speed = min(1.5, max(0.5, float(s["speed"])))
        engine = s["engine"] if s["engine"] in ENGINES else "kokoro"
        samples: Optional[np.ndarray] = None
        rate = SAMPLE_RATE
        self.note = ""
        if engine == "voicebox":
            try:
                if not s["voice"]:
                    raise RuntimeError("no Voicebox voice picked yet")
                vid = str(s["voice"])
                if vid.startswith("preset:"):              # previewing a preset: it becomes a profile once
                    vid = await self._voicebox_voice(vid, False)
                data = await self.voicebox.speech(text, vid, speed, str(s.get("delivery") or ""))
                samples, rate = wav_to_samples(data)
            except Exception as exc:
                log.info("Voicebox speech failed (%s); using Kokoro", exc)
                self.note = ("Voicebox isn't running, so Orion used its built-in voice."
                             if not (await self.voicebox_state(fresh=True))["reachable"]
                             else f"Voicebox couldn't speak ({exc}), so Orion used its built-in voice.")
                engine = "kokoro"
        elif engine == "piper":
            try:
                models = {p.stem: p for p in piper_models()}
                model = models.get(str(s["voice"])) or next(iter(models.values()))
                samples, rate = await asyncio.to_thread(piper_synth, text, model, speed)
            except Exception as exc:
                self.note = f"Piper couldn't speak ({exc}), so Orion used its built-in voice."
                engine = "kokoro"
        if samples is None:
            kv = str(s["voice"]) if s["voice"] in KOKORO_IDS else DEFAULTS["voice"]
            samples = await kokoro_samples(daemon, text, kv, speed)
            rate = SAMPLE_RATE
            engine = "kokoro"
        samples = resample(np.asarray(samples, dtype=np.float32), rate, SAMPLE_RATE)
        warmth = float(s.get("warmth") or 0)
        if warmth > 0:
            samples = await asyncio.to_thread(warm, samples, warmth)
        return samples, SAMPLE_RATE, engine

    async def speak_and_stream(self, daemon: Any, text: str, voice: Optional[str] = None,
                               speed: Optional[float] = None) -> dict[str, Any]:
        """Orion's normal speaking path: plays in the HUD (AUDIO_PLAY) with the waveform animation."""
        s = self.settings()
        # The HUD's SPEAK event always sends its old built-in defaults; they mean "Orion's chosen voice".
        voice = None if voice in (None, "", "af_nicole") else voice
        speed = None if speed is None or abs(float(speed) - 0.92) < 1e-6 else float(speed)
        ae = getattr(daemon, "audio_engine", None)
        bus = getattr(daemon, "bus", None)
        if s["engine"] == "kokoro" and not float(s.get("warmth") or 0) and ae is not None:
            # unchanged behaviour: Kokoro streams itself
            await ae.speak_and_stream(text, bus, voice=voice or s["voice"], speed=speed or s["speed"])
            return {"engine": "kokoro", "note": ""}
        over = {"voice": voice, "speed": speed} if s["engine"] == "kokoro" else {"speed": speed}
        samples, rate, used = await self.synthesize(daemon, text, over)
        await publish_audio(daemon, text, samples, rate, used)
        return {"engine": used, "note": self.note}

    async def speak_wav(self, daemon: Any, text: str) -> Optional[bytes]:
        """WAV bytes for the phone, or None (the phone then uses its own voice)."""
        try:
            samples, rate, _ = await self.synthesize(daemon, text)
            return samples_to_wav(samples, rate)
        except Exception as exc:
            log.warning("Voice for the phone failed: %s", exc)
            return None


async def kokoro_samples(daemon: Any, text: str, voice: str, speed: float) -> np.ndarray:
    ae = getattr(daemon, "audio_engine", None)
    if ae is None or not hasattr(ae, "_synthesize_array"):
        raise RuntimeError("Orion's built-in voice isn't installed.")
    await ae.ensure_initialized()
    return await asyncio.to_thread(ae._synthesize_array, text, voice, speed)


async def publish_audio(daemon: Any, text: str, samples: np.ndarray, rate: int, engine: str) -> None:
    bus = getattr(daemon, "bus", None)
    if bus is None:
        return
    sid = uuid.uuid4().hex[:10]
    await bus.publish({"event": "AUDIO_PLAY", "stream_id": sid, "text": text, "engine": engine,
                       "audio_base64": base64.b64encode(samples_to_wav(samples, rate)).decode("ascii"),
                       "sample_rate": rate, "duration_s": round(len(samples) / rate, 2)}, record=False)
    ae = getattr(daemon, "audio_engine", None)
    wave_fn = getattr(ae, "broadcast_waveform", None)
    if callable(wave_fn):
        asyncio.create_task(wave_fn(samples, bus, sid))


# ---------------------------------------------------------------- one instance per daemon
def get(daemon: Any) -> VoiceEngines:
    ve = getattr(daemon, "voice_engines", None)
    if ve is None:
        from config import settings
        ve = VoiceEngines(settings.data_dir)
        try:
            setattr(daemon, "voice_engines", ve)
        except Exception:
            pass
    return ve


def make_router(get_daemon: Callable[[], Any]):
    r = APIRouter(prefix="/voice", tags=["voice"])

    async def body(request: Request) -> dict[str, Any]:
        try:
            b = await request.json()
        except ValueError:
            b = None
        return b if isinstance(b, dict) else {}

    def fail(exc: Exception) -> JSONResponse:
        code = 403 if isinstance(exc, PermissionError) else 503 if isinstance(exc, RuntimeError) else 400
        return JSONResponse({"ok": False, "error": str(exc), "message": str(exc)}, status_code=code)

    @r.get("/engines")
    async def engines():
        return await get(get_daemon()).engines()

    @r.post("/set")
    async def set_voice(request: Request):
        try:
            return await get(get_daemon()).set(await body(request))
        except (ValueError, RuntimeError, PermissionError) as exc:
            return fail(exc)

    @r.post("/preview")
    async def preview(request: Request):
        """Speak a sample. Optional overrides (engine, voice, speed, warmth, delivery) are tried without saving.
        out="hud" (default) plays it in the Orion window; out="wav" returns it (OrionOS first-run plays it)."""
        b = await body(request)
        d = get_daemon()
        ve = get(d)
        text = str(b.get("text") or SAMPLE_LINE).strip()[:400] or SAMPLE_LINE
        over = {k: b.get(k) for k in ("engine", "voice", "speed", "warmth", "delivery") if b.get(k) is not None}
        if over.get("engine") is not None and over["engine"] not in ENGINES:
            return fail(ValueError("Pick kokoro, voicebox or piper."))
        try:
            samples, rate, used = await ve.synthesize(d, text, over)
        except Exception as exc:
            return fail(RuntimeError(f"Orion couldn't speak: {exc}"))
        out: dict[str, Any] = {"ok": True, "engine": used, "note": ve.note,
                               "duration_s": round(len(samples) / rate, 2)}
        if str(b.get("out") or "hud") == "wav":
            out["audio_base64"] = base64.b64encode(samples_to_wav(samples, rate)).decode("ascii")
        else:
            await publish_audio(d, text, samples, rate, used)
        return out

    @r.post("/design")
    async def design(request: Request):
        try:
            return await get(get_daemon()).design(await body(request))
        except (ValueError, RuntimeError, PermissionError) as exc:
            return fail(exc)

    @r.post("/hear")
    async def hear(request: Request):
        """A short recording of you (a few seconds, kept in memory only) -> the words Orion heard."""
        data = await request.body()
        if not data or len(data) > 4 * 1024 * 1024:
            return fail(ValueError("Send a short recording (up to a few seconds)."))
        transcribe = getattr(get_daemon(), "transcribe", None)
        text = await transcribe(data) if callable(transcribe) else None
        if text is None:
            return fail(RuntimeError("Hearing needs the voice add-on (Settings > Set up my voice)."))
        return {"ok": True, "text": text}

    return r
