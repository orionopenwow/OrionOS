"""Perceptual Capture Engine.

Every `interval` seconds: grab the primary monitor with mss, compute a 64-bit
pHash, and compare it against the last *processed* frame. Visually identical
frames (Hamming distance < threshold) are dropped before OCR/LLM. Changed frames
go through local Tesseract OCR; raw pixels are discarded and only text is emitted.

Comparing against the last processed frame (rather than strictly t-1) means slow,
incremental changes (e.g. typing) still accumulate into a delta eventually.
A foreground-window change always forces processing (event-driven focus hook).
"""
from __future__ import annotations

import ctypes
import logging
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import asdict, dataclass
from typing import Any, Callable, Optional

import imagehash
import mss
import pytesseract
from PIL import Image

from config import settings
import capture_privacy  # B7: never read password managers, banking, private windows, the lock screen
import linux_desktop

log = logging.getLogger("orion.capture")

_WIN_TESSERACT_PATHS = (
    r"C:\Program Files\Tesseract-OCR\tesseract.exe",
    r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
    os.path.expandvars(r"%LOCALAPPDATA%\Programs\Tesseract-OCR\tesseract.exe"),
)


def configure_tesseract() -> bool:
    """Locate the tesseract binary. Returns True if OCR is usable."""
    if settings.tesseract_cmd:
        pytesseract.pytesseract.tesseract_cmd = settings.tesseract_cmd
    elif not shutil.which("tesseract") and sys.platform == "win32":
        for candidate in _WIN_TESSERACT_PATHS:
            if os.path.exists(candidate):
                pytesseract.pytesseract.tesseract_cmd = candidate
                break
    try:
        version = pytesseract.get_tesseract_version()
        log.info("Tesseract %s ready", version)
        return True
    except Exception:
        log.warning("Tesseract not found — OCR disabled (pHash diffing still active). "
                    "Install it or set TESSERACT_CMD.")
        return False


def active_window_title() -> Optional[str]:
    """Foreground window title via native OS primitives (no polling threads)."""
    try:
        if sys.platform == "win32":
            user32 = ctypes.windll.user32
            hwnd = user32.GetForegroundWindow()
            length = user32.GetWindowTextLengthW(hwnd)
            buf = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buf, length + 1)
            return buf.value or None
        if sys.platform == "darwin":
            script = 'tell application "System Events" to get name of first application process whose frontmost is true'
            out = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=2)
            return out.stdout.strip() or None
        if linux_desktop.has_compositor():  # [look] Sway or Hyprland
            return linux_desktop.active_window_title()
        if shutil.which("xdotool"):
            out = subprocess.run(["xdotool", "getactivewindow", "getwindowname"],
                                 capture_output=True, text=True, timeout=2)
            return out.stdout.strip() or None
    except Exception:
        log.debug("active_window_title failed", exc_info=True)
    return None


@dataclass
class CaptureStats:
    frames_total: int = 0
    frames_skipped: int = 0
    frames_processed: int = 0
    ocr_chars: int = 0
    est_tokens_saved: int = 0
    last_phash_diff: Optional[int] = None

    @property
    def skip_ratio(self) -> float:
        return self.frames_skipped / self.frames_total if self.frames_total else 0.0


class PerceptualCaptureEngine(threading.Thread):
    def __init__(
        self,
        on_delta: Callable[[dict[str, Any]], None],
        interval: float = settings.capture_interval,
        threshold: int = settings.phash_threshold,
        monitor_index: int = settings.monitor_index,
    ) -> None:
        super().__init__(name="orion-capture", daemon=True)
        self.on_delta = on_delta
        self.interval = max(0.5, interval)
        self.threshold = threshold
        self.monitor_index = monitor_index
        self.ocr_available = configure_tesseract()
        self.stats = CaptureStats()
        self._stats_lock = threading.Lock()
        self._stop_evt = threading.Event()
        self._paused = threading.Event()
        self._prev_hash: Optional[imagehash.ImageHash] = None
        self._prev_title: Optional[str] = None
        # V2 pacing: wall-clock time of the last meaningful pHash delta / focus change.
        self.last_change_ts: Optional[float] = None
        # Feature Bible D5: window switches are a natural pause for wellness check-ins.
        self.last_focus_change_ts: Optional[float] = None
        self.last_skip_reason = ""   # B7: why the last frame wasn't read ("a password manager"), or ""

    # ------------------------------------------------------------ control
    def pause(self) -> None:
        self._paused.set()
        log.info("Capture paused")

    def resume(self) -> None:
        self._paused.clear()
        self._prev_hash = None  # force a fresh baseline
        log.info("Capture resumed")

    @property
    def paused(self) -> bool:
        return self._paused.is_set()

    def stop(self) -> None:
        self._stop_evt.set()

    def snapshot(self) -> dict[str, Any]:
        with self._stats_lock:
            data = asdict(self.stats)
            data["skip_ratio"] = round(self.stats.skip_ratio, 4)
        return data

    # --------------------------------------------------------------- loop
    def run(self) -> None:
        log.info("Perceptual capture engine started (interval=%.1fs, threshold=%d)", self.interval, self.threshold)
        if linux_desktop.is_wayland():
            # Wayland: mss (X11) can't see the screen; grim is stateless, so no handle to hold.
            self._run_loop(None)
        else:
            try:
                sct_ctx = mss.mss()  # mss handles must live on the capture thread
            except Exception as exc:  # e.g. Linux over SSH: no X display and no Wayland session
                log.warning("Screen capture unavailable (%s); perceptual capture stays off", exc)
                return
            with sct_ctx as sct:
                self._run_loop(sct)
        log.info("Perceptual capture engine stopped")

    def _run_loop(self, sct: Optional[mss.base.MSSBase]) -> None:
        try:
            self._loop(sct)
        finally:
            capture_privacy.indicate(False)

    def _loop(self, sct: Optional[mss.base.MSSBase]) -> None:
        while not self._stop_evt.is_set():
            started = time.monotonic()
            capture_privacy.indicate(not self._paused.is_set())   # the visible "reading the screen" sign
            if not self._paused.is_set():
                try:
                    self._tick(sct)
                except Exception:
                    log.exception("Capture tick failed")
            self._stop_evt.wait(max(0.0, self.interval - (time.monotonic() - started)))

    def _grab(self, sct: Optional[mss.base.MSSBase]) -> Image.Image:
        if sct is None:
            frame = linux_desktop.grab_screen()
            if frame is None:
                raise RuntimeError("grim screenshot unavailable")
            return frame
        monitors = sct.monitors
        idx = self.monitor_index if 0 <= self.monitor_index < len(monitors) else min(1, len(monitors) - 1)
        shot = sct.grab(monitors[idx])
        return Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")

    def _tick(self, sct: Optional[mss.base.MSSBase]) -> None:
        # B7 exclusions: checked before any screenshot is taken, so nothing of these screens is ever grabbed.
        why = capture_privacy.skip_reason(active_window_title())
        if why:
            self.last_skip_reason = why
            with self._stats_lock:
                self.stats.frames_total += 1
                self.stats.frames_skipped += 1
            self._prev_hash = None   # read afresh once it's gone
            return
        self.last_skip_reason = ""
        try:
            frame = self._grab(sct)
        except Exception as exc:
            log.debug("Screen grab skipped (display headless or locked): %s", exc)
            time.sleep(1.0)
            return

        current = imagehash.phash(frame)
        title = active_window_title()
        diff = 64 if self._prev_hash is None else int(current - self._prev_hash)
        focus_changed = self._prev_title is not None and title != self._prev_title
        if focus_changed:
            self.last_focus_change_ts = time.time()
        self._prev_title = title

        with self._stats_lock:
            self.stats.frames_total += 1
            self.stats.last_phash_diff = diff

        if diff < self.threshold and not focus_changed:
            log.info("Frame visually identical (Δ=%d). Skipping OCR/LLM", diff)
            with self._stats_lock:
                self.stats.frames_skipped += 1
                self.stats.est_tokens_saved += settings.image_token_estimate
            return

        self._prev_hash = current
        self.last_change_ts = time.time()
        text = self._ocr(frame)
        del frame  # raw imagery is never retained

        with self._stats_lock:
            self.stats.frames_processed += 1
            self.stats.ocr_chars += len(text)
            # Sending text instead of the screenshot: ~4 chars per token.
            self.stats.est_tokens_saved += max(0, settings.image_token_estimate - len(text) // 4)

        self.on_delta({
            "event": "SCREEN_DELTA_DETECTED",
            "timestamp": time.time(),
            "phash_diff": diff,
            "extracted_text": text,
            "window_title": title,
            "focus_changed": focus_changed,
            "ocr_available": self.ocr_available,
        })

    def _ocr(self, frame: Image.Image) -> str:
        if not self.ocr_available:
            return ""
        gray = frame.convert("L")
        if gray.width > 2560:  # keep OCR latency bounded on 4K+ displays
            ratio = 2560 / gray.width
            gray = gray.resize((2560, int(gray.height * ratio)), Image.LANCZOS)
        try:
            raw = pytesseract.image_to_string(gray, timeout=30)
        except RuntimeError:
            log.warning("OCR timed out")
            return ""
        except Exception:
            log.exception("OCR failed")
            return ""
        lines = [re.sub(r"[ \t]+", " ", ln).strip() for ln in raw.splitlines()]
        text = "\n".join(ln for ln in lines if len(ln) > 1)
        return text[: settings.ocr_max_chars]
