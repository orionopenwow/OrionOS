"""Audio Engine powered by local Kokoro TTS & soundfile.
Supports voice blending (af_nicole + af_heart), WAV streaming over WebSocket bus,
and real-time FFT / amplitude broadcasting for synchronized frontend waveform modulation.
"""
from __future__ import annotations

import asyncio
import base64
import io
import logging
import time
import uuid
from typing import Any, Optional

import numpy as np

# torch / soundfile are imported lazily so the daemon (HUD, comms, memory, agents) still starts
# when the optional voice stack is missing or broken — only speech synthesis is then unavailable.


class _LazySoundfile:
    def __getattr__(self, name: str) -> Any:
        import soundfile

        return getattr(soundfile, name)


sf = _LazySoundfile()

log = logging.getLogger("orion.audio_engine")

SAMPLE_RATE = 24000
DEFAULT_SPEED = 0.92
DEFAULT_VOICE = "af_nicole"


class AudioEngine:
    def __init__(self, lang_code: str = "a", repo_id: str = "hexgrad/Kokoro-82M") -> None:
        self.lang_code = lang_code
        self.repo_id = repo_id
        self.pipeline: Optional[Any] = None
        self._voice_cache: dict[str, Any] = {}
        self._blend_voice: Optional[Any] = None
        self._is_ready = False
        self._init_lock = asyncio.Lock()

    def initialize_sync(self) -> None:
        """Synchronously initialize Kokoro pipeline and preload blended voices."""
        if self._is_ready:
            return
        try:
            from kokoro import KPipeline

            log.info("Initializing Kokoro KPipeline(lang_code='%s', repo_id='%s')...", self.lang_code, self.repo_id)
            self.pipeline = KPipeline(lang_code=self.lang_code, repo_id=self.repo_id)

            # Preload and blend af_nicole and af_heart for a calm, gentle, empathetic female cadence
            try:
                self._blend_voice = self.pipeline.load_voice("af_nicole,af_heart")
                log.info("Kokoro voice blend loaded successfully ('af_nicole,af_heart')")
            except Exception as blend_err:
                log.warning("Direct 'af_nicole,af_heart' blend failed: %s; falling back to af_nicole", blend_err)
                try:
                    v1 = self.pipeline.load_voice("af_nicole")
                    v2 = self.pipeline.load_voice("af_heart")
                    self._blend_voice = 0.5 * v1 + 0.5 * v2
                    log.info("Custom weighted tensor blend loaded (50% af_nicole + 50% af_heart)")
                except Exception:
                    self._blend_voice = self.pipeline.load_voice("af_nicole")
                    log.info("Fallback default voice 'af_nicole' loaded")

            self._voice_cache["af_nicole"] = self._blend_voice
            self._voice_cache["blend"] = self._blend_voice
            self._is_ready = True
            log.info("AudioEngine initialized successfully.")
        except Exception as exc:
            log.error("Failed to initialize Kokoro AudioEngine: %s", exc)
            self._is_ready = False

    async def ensure_initialized(self) -> None:
        if self._is_ready:
            return
        async with self._init_lock:
            if not self._is_ready:
                await asyncio.to_thread(self.initialize_sync)

    def _get_voice(self, voice_name: str) -> Any:
        if not self.pipeline:
            raise RuntimeError("Kokoro pipeline is not initialized")

        if voice_name in ("af_nicole", "blend", "calm_empathetic", "default"):
            return self._blend_voice

        if voice_name not in self._voice_cache:
            try:
                self._voice_cache[voice_name] = self.pipeline.load_voice(voice_name)
            except Exception as exc:
                log.warning("Could not load voice '%s': %s; using blend voice", voice_name, exc)
                return self._blend_voice

        return self._voice_cache[voice_name]

    def _synthesize_array(self, text: str, voice: str = DEFAULT_VOICE, speed: float = DEFAULT_SPEED) -> np.ndarray:
        """Synthesize text and return concatenated float32 numpy audio samples."""
        if not self._is_ready:
            self.initialize_sync()
        if not self.pipeline:
            raise RuntimeError("AudioEngine pipeline failed to load")

        voice_data = self._get_voice(voice)
        audio_segments: list[np.ndarray] = []

        results = self.pipeline(text, voice=voice_data, speed=speed)
        for res in results:
            if hasattr(res, "audio") and res.audio is not None:
                audio = res.audio
                if hasattr(audio, "detach"):  # torch.Tensor
                    arr = audio.detach().cpu().numpy().astype(np.float32)
                else:
                    arr = np.asarray(audio, dtype=np.float32)
                audio_segments.append(arr)

        if not audio_segments:
            return np.zeros(SAMPLE_RATE // 4, dtype=np.float32)

        return np.concatenate(audio_segments)

    def speak(self, text: str, voice: str = DEFAULT_VOICE, speed: float = DEFAULT_SPEED) -> bytes:
        """Synthesize speech and return complete WAV bytes."""
        samples = self._synthesize_array(text, voice=voice, speed=speed)
        buf = io.BytesIO()
        sf.write(buf, samples, SAMPLE_RATE, format="WAV")
        return buf.getvalue()

    async def speak_async(self, text: str, voice: str = DEFAULT_VOICE, speed: float = DEFAULT_SPEED) -> bytes:
        await self.ensure_initialized()
        return await asyncio.to_thread(self.speak, text, voice, speed)

    async def broadcast_waveform(
        self,
        samples: np.ndarray,
        bus: Any,
        stream_id: str,
        chunk_size: int = 1024,
    ) -> None:
        """Stream real-time FFT / amplitude chunks across the event bus in sync with audio duration."""
        total_samples = len(samples)
        chunk_duration = chunk_size / SAMPLE_RATE

        # Precompute window
        window = np.hanning(chunk_size)

        for i in range(0, total_samples, chunk_size):
            chunk = samples[i : i + chunk_size]
            if len(chunk) < chunk_size:
                chunk = np.pad(chunk, (0, chunk_size - len(chunk)))

            # 1. Amplitude (RMS scaled)
            rms = float(np.sqrt(np.mean(chunk**2)))
            amplitude = min(1.0, rms * 3.5)

            # 2. FFT frequency spectrum (64 discrete bars normalized)
            fft_data = np.abs(np.fft.rfft(chunk * window))
            num_bins = 64
            bin_size = max(1, len(fft_data) // num_bins)
            bars: list[float] = []
            for b in range(num_bins):
                val = float(np.mean(fft_data[b * bin_size : (b + 1) * bin_size]))
                # Log scaling for visual frequency response
                norm_val = min(1.0, max(0.0, float(np.log1p(val * 4.0) / 3.0)))
                bars.append(round(norm_val, 4))

            await bus.publish(
                {
                    "event": "AUDIO_WAVEFORM_DATA",
                    "stream_id": stream_id,
                    "amplitude": round(amplitude, 4),
                    "fft": bars,
                    "is_playing": True,
                },
                record=False,
            )

            await asyncio.sleep(chunk_duration)

        # Signal completion
        await bus.publish(
            {
                "event": "AUDIO_WAVEFORM_DATA",
                "stream_id": stream_id,
                "amplitude": 0.0,
                "fft": [0.0] * 64,
                "is_playing": False,
            },
            record=False,
        )

    async def speak_and_stream(
        self,
        text: str,
        bus: Any,
        voice: str = DEFAULT_VOICE,
        speed: float = DEFAULT_SPEED,
        stream_id: Optional[str] = None,
    ) -> bytes:
        """Synthesizes audio, transmits the WAV stream over WebSocket bus,

        and runs real-time FFT/amplitude animation in lockstep.
        """
        await self.ensure_initialized()
        stream_id = stream_id or uuid.uuid4().hex[:10]

        # Synthesize in thread pool
        samples = await asyncio.to_thread(self._synthesize_array, text, voice, speed)

        # Convert to WAV
        buf = io.BytesIO()
        sf.write(buf, samples, SAMPLE_RATE, format="WAV")
        wav_bytes = buf.getvalue()
        b64_audio = base64.b64encode(wav_bytes).decode("ascii")

        # Notify client of playback start with the audio stream
        await bus.publish(
            {
                "event": "AUDIO_PLAY",
                "stream_id": stream_id,
                "text": text,
                "audio_base64": b64_audio,
                "sample_rate": SAMPLE_RATE,
                "duration_s": round(len(samples) / SAMPLE_RATE, 2),
            },
            record=False,
        )

        # Broadcast waveform animation chunks concurrently
        asyncio.create_task(self.broadcast_waveform(samples, bus, stream_id))

        return wav_bytes


audio_engine = AudioEngine()
