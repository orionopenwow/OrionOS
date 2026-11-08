"""Local Text-to-Speech (TTS) synthesizer with voice selection & personality.
Supports local pyttsx3 OS synthesis with zero cloud latency and voice switching.
"""
from __future__ import annotations

import asyncio
import logging
import sys
from typing import Any, Optional

log = logging.getLogger("orion.tts")


class LocalTTS:
    def __init__(self, voice_engine: str = "auto", default_voice: Optional[str] = None) -> None:
        self.voice_engine = voice_engine
        self._engine = None
        self._available = False
        self._current_voice_id = default_voice
        self._rate = 185
        self._volume = 0.9
        self._personality_mode = "concise_companion"  # or 'detailed_researcher'
        self._init_engine()

    def _init_engine(self) -> None:
        try:
            import pyttsx3
            self._engine = pyttsx3.init()
            self._engine.setProperty("rate", self._rate)
            self._engine.setProperty("volume", self._volume)

            # If default_voice requested, attempt to apply it
            if self._current_voice_id:
                self.set_voice(self._current_voice_id)
            else:
                current = self._engine.getProperty("voice")
                self._current_voice_id = current

            self._available = True
            log.info("Local native OS TTS engine initialized (voice=%s, rate=%d)", self._current_voice_id, self._rate)
        except Exception as exc:
            log.warning("pyttsx3 native engine unavailable: %s", exc)

    @property
    def is_available(self) -> bool:
        return self._available

    def get_available_voices(self) -> list[dict[str, Any]]:
        """Returns list of installed OS voices with their properties."""
        if not self._engine:
            return []
        try:
            voices = self._engine.getProperty("voices")
            result = []
            for v in voices:
                result.append({
                    "id": v.id,
                    "name": getattr(v, "name", v.id),
                    "languages": getattr(v, "languages", []),
                    "gender": getattr(v, "gender", None),
                    "age": getattr(v, "age", None),
                })
            return result
        except Exception as exc:
            log.warning("Failed to list voices: %s", exc)
            return []

    def set_voice(self, voice_id: str) -> bool:
        if not self._engine or not voice_id:
            return False
        try:
            self._engine.setProperty("voice", voice_id)
            self._current_voice_id = voice_id
            log.info("TTS voice set to: %s", voice_id)
            return True
        except Exception as exc:
            log.warning("Failed to set TTS voice %s: %s", voice_id, exc)
            return False

    def set_rate(self, rate: int) -> bool:
        if not self._engine:
            return False
        try:
            clamped = max(100, min(350, int(rate)))
            self._rate = clamped
            self._engine.setProperty("rate", clamped)
            return True
        except Exception as exc:
            log.warning("Failed to set rate: %s", exc)
            return False

    def set_volume(self, volume: float) -> bool:
        if not self._engine:
            return False
        try:
            clamped = max(0.0, min(1.0, float(volume)))
            self._volume = clamped
            self._engine.setProperty("volume", clamped)
            return True
        except Exception as exc:
            log.warning("Failed to set volume: %s", exc)
            return False

    def set_personality_mode(self, mode: str) -> None:
        """Adjust pacing and pitch dynamically based on personality tone."""
        self._personality_mode = mode
        if mode == "concise_companion":
            # Snappy, upbeat, friendly pacing
            self.set_rate(195)
        elif mode == "detailed_researcher":
            # Deliberate, clear, measured academic pacing
            self.set_rate(170)

    @property
    def current_settings(self) -> dict[str, Any]:
        return {
            "available": self._available,
            "current_voice_id": self._current_voice_id,
            "rate": self._rate,
            "volume": self._volume,
            "personality_mode": self._personality_mode,
        }

    def speak_sync(self, text: str) -> None:
        if not self._engine:
            return
        if not hasattr(self, "_lock"):
            import threading
            self._lock = threading.Lock()
        acquired = self._lock.acquire(blocking=False)
        if not acquired:
            log.debug("TTS is already speaking, skipping concurrent playback")
            return
        try:
            self._engine.say(text)
            self._engine.runAndWait()
        except Exception as exc:
            log.warning("TTS playback error: %s", exc)
            try:
                self._engine.endLoop()
            except Exception:
                pass
        finally:
            self._lock.release()

    async def speak(self, text: str) -> None:
        """Asynchronously synthesizes speech in a background thread."""
        if not self._available or not text.strip():
            return
        await asyncio.to_thread(self.speak_sync, text)
