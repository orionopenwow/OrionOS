"""Local Speech-to-Text (STT) pipeline with Voice Activity Detection (VAD).
Transcribes audio input locally with zero cloud dependencies using faster-whisper.
"""
from __future__ import annotations

import asyncio
import collections
import logging
import queue
import threading
import time
from typing import Callable, Optional

import numpy as np

log = logging.getLogger("orion.stt")

SAMPLE_RATE = 16000
FRAME_DURATION_MS = 30  # 10, 20, or 30 ms for webrtcvad
FRAME_SIZE = int(SAMPLE_RATE * FRAME_DURATION_MS / 1000)


class WhisperSTTEngine(threading.Thread):
    def __init__(
        self,
        on_transcript: Callable[[str], None],
        model_size: str = "base.en",
        device: str = "cpu",
        compute_type: str = "int8",
        vad_mode: int = 3,  # 0 (least aggressive) to 3 (most aggressive)
    ) -> None:
        super().__init__(name="orion-stt", daemon=True)
        self.on_transcript = on_transcript
        self.model_size = model_size
        self.device = device
        self.compute_type = compute_type
        self.vad_mode = vad_mode
        self._stop_event = threading.Event()
        self._paused = threading.Event()
        self._model = None
        self._vad = None
        self._audio_queue: queue.Queue[bytes] = queue.Queue()
        self.is_ready = False

    def initialize_model(self) -> bool:
        try:
            import webrtcvad
            from faster_whisper import WhisperModel

            log.info("Loading local Whisper STT model '%s' (%s, %s)...", self.model_size, self.device, self.compute_type)
            self._model = WhisperModel(self.model_size, device=self.device, compute_type=self.compute_type)
            self._vad = webrtcvad.Vad(self.vad_mode)
            self.is_ready = True
            log.info("Whisper STT engine initialized successfully")
            return True
        except Exception as exc:
            hint = (" Voice typing is off: its add-ons aren't installed (pip install -r requirements.txt in Orion's "
                    "Python, or orion-ctl setup voice on OrionOS).") if isinstance(exc, ImportError) else ""
            log.warning("Whisper STT initialization failed: %s.%s", exc, hint)
            self.is_ready = False
            return False

    def pause(self) -> None:
        self._paused.set()

    def resume(self) -> None:
        self._paused.clear()

    def stop(self) -> None:
        self._stop_event.set()

    def transcribe_audio_buffer(self, audio_data: np.ndarray) -> str:
        if not self._model:
            return ""
        try:
            segments, _ = self._model.transcribe(
                audio_data,
                beam_size=5,
                language="en",
                condition_on_previous_text=False,
            )
            text = " ".join([segment.text for segment in segments]).strip()
            return text
        except Exception as exc:
            log.warning("Transcription error: %s", exc)
            return ""

    def run(self) -> None:
        if not self.initialize_model():
            return

        try:
            import sounddevice as sd
        except Exception as exc:
            log.warning("sounddevice module unavailable: %s", exc)
            return

        def audio_callback(indata, frames, _time, status):
            if status:
                log.debug("Audio input status: %s", status)
            if not self._paused.is_set():
                # indata is float32 [-1, 1], convert to int16 PCM for webrtcvad
                pcm16 = (indata[:, 0] * 32767).astype(np.int16).tobytes()
                self._audio_queue.put(pcm16)

        try:
            stream = sd.RawInputStream(
                samplerate=SAMPLE_RATE,
                blocksize=FRAME_SIZE,
                dtype="float32",
                channels=1,
                callback=audio_callback,
            )
        except Exception as exc:
            log.warning("Failed to open audio input stream: %s", exc)
            return

        log.info("Whisper STT microphone listener running (VAD mode %d)", self.vad_mode)

        ring_buffer = collections.deque(maxlen=int(300 / FRAME_DURATION_MS))
        voiced_frames: list[bytes] = []
        is_triggered = False

        with stream:
            while not self._stop_event.is_set():
                try:
                    frame = self._audio_queue.get(timeout=0.1)
                except queue.Empty:
                    continue

                if self._paused.is_set():
                    ring_buffer.clear()
                    voiced_frames.clear()
                    is_triggered = False
                    continue

                is_speech = self._vad.is_speech(frame, SAMPLE_RATE)

                if not is_triggered:
                    ring_buffer.append((frame, is_speech))
                    num_voiced = len([f for f, speech in ring_buffer if speech])
                    if num_voiced > 0.8 * ring_buffer.maxlen:
                        is_triggered = True
                        voiced_frames.extend([f for f, _ in ring_buffer])
                        ring_buffer.clear()
                else:
                    voiced_frames.append(frame)
                    ring_buffer.append((frame, is_speech))
                    num_unvoiced = len([f for f, speech in ring_buffer if not speech])
                    if num_unvoiced > 0.9 * ring_buffer.maxlen:
                        # End of speech detected; transcribe accumulated audio
                        is_triggered = False
                        pcm_bytes = b"".join(voiced_frames)
                        voiced_frames.clear()
                        ring_buffer.clear()

                        if len(pcm_bytes) > SAMPLE_RATE * 0.6 * 2:  # Min 600ms of audio
                            audio_np = np.frombuffer(pcm_bytes, dtype=np.int16).astype(np.float32) / 32768.0
                            transcription = self.transcribe_audio_buffer(audio_np)
                            if transcription:
                                log.info("Voice transcribed: '%s'", transcription)
                                self.on_transcript(transcription)
