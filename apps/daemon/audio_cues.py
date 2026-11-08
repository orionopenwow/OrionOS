"""Cosmic Sound Effects & Audio Cues manager for Orion.

Dispatches audio cue events over the IPC bus and plays subtle native chimes.
Cues:
  - 'listen_start': ethereal cosmic chime when Orion begins listening
  - 'command_detected': resonant crystal chime when speech/command is detected
  - 'task_finished': gentle cosmic resolution chime when reasoning/task finishes
  - 'proactive_alert': celestial shimmer when proactive insight is noticed
  - 'celestial_chime': harmonic pacing chime at 90 minutes of unbroken desk time (V2)
  - 'approval_required': soft two-note ping when a draft lands in the approval queue (V2)
"""
from __future__ import annotations

import asyncio
import logging
import sys
import threading
from typing import Any, Callable, Optional

log = logging.getLogger("orion.audio_cues")


class AudioCuesManager:
    def __init__(self, broadcast_fn: Optional[Callable[[dict[str, Any]], Any]] = None) -> None:
        self.broadcast_fn = broadcast_fn
        self.enabled = True

    async def emit(self, cue_type: str, detail: Optional[str] = None) -> None:
        """Broadcasts audio cue event to all connected HUD clients."""
        if not self.enabled:
            return
        payload = {
            "event": "AUDIO_CUE",
            "cue": cue_type,
            "detail": detail or "",
        }
        if self.broadcast_fn:
            try:
                res = self.broadcast_fn(payload)
                if asyncio.iscoroutine(res):
                    await res
            except Exception as exc:
                log.debug("Failed to broadcast audio cue %s: %s", cue_type, exc)

        # Native fallback beep on Windows if headless
        self._native_beep_fallback(cue_type)

    def _native_beep_fallback(self, cue_type: str) -> None:
        """Subtle non-blocking native beep if no browser/HUD client is connected."""
        def _play():
            try:
                if sys.platform == "win32":
                    import winsound
                    if cue_type == "listen_start":
                        winsound.Beep(1046, 70)  # C6
                        winsound.Beep(1318, 90)  # E6
                    elif cue_type == "command_detected":
                        winsound.Beep(1567, 80)  # G6
                    elif cue_type == "task_finished":
                        winsound.Beep(1318, 60)  # E6
                        winsound.Beep(1046, 100) # C6
                    elif cue_type == "proactive_alert":
                        winsound.Beep(1760, 60)  # A6
                    elif cue_type == "celestial_chime":
                        for f, d in ((784, 120), (988, 120), (1175, 160), (1568, 260)):  # G5 B5 D6 G6
                            winsound.Beep(f, d)
                    elif cue_type == "approval_required":
                        winsound.Beep(1318, 70)  # E6
                        winsound.Beep(1568, 90)  # G6
            except Exception:
                pass

        threading.Thread(target=_play, daemon=True).start()


audio_cues = AudioCuesManager()
