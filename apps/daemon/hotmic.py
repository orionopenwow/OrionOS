"""Hot mic: Orion listens all the time, but only acts on your voice (release plan, Step 5).

Every 30 ms of sound goes through four checks, all on this machine:

  1. Loudness: only sound inside the decibel range you set (`orion-ctl voice calibrate`) counts.
  2. Speech: a voice-activity detector (webrtcvad) drops fans, music and keyboards.
  3. You: your voice profile (`orion-ctl voice enroll`, ~20 short lines) is compared with each phrase by a
     speaker-check model (SpeechBrain ECAPA). Anyone else is ignored.
  4. A command: Whisper turns it into text. By default it must start with "Orion" ("Orion, start the server").
     Then it goes to quick commands or chat, exactly like typing it.

What voice can never do: approve anything, install anything, touch money. A recording of your voice could fool
a voice check, so approvals stay on your phone's fingerprint. Nothing is recorded or saved: only the last 2 seconds
are held in memory, to catch the start of a sentence. Ctrl+Alt+M mutes it instantly; locking the screen mutes it.

CLI (runs in the daemon's Python, as you):
  python hotmic.py enroll       record your voice profile
  python hotmic.py calibrate    set the loudness range from your room and your voice
"""
from __future__ import annotations

import collections
import json
import logging
import math
import os
import re
import threading
import time
from pathlib import Path
from typing import Any, Callable, Optional

import numpy as np

log = logging.getLogger("orion.hotmic")

RATE = 16000
FRAME = 480                 # 30 ms
PREROLL_S = 2.0
END_SILENCE_FRAMES = 25     # 750 ms of quiet ends a phrase
START_FRAMES = 3            # 90 ms of loud-enough speech starts one
MAX_UTTERANCE_S = 10.0
MIN_UTTERANCE_S = 0.5
DEFAULTS = {"enabled": False, "min_db": -42.0, "max_db": -3.0, "require_wake": True,
            "wake_word": False, "wake_threshold": 0.5, "wyoming_port": 0}   # [v1gaps] openWakeWord (wake_word.py), off by default
WAKE_RE = re.compile(r"^\s*(?:hey|ok|okay)?[\s,]*orion\b[\s,.!?:;-]*", re.I)
ENROLL_LINES = [
    "Orion, start the server.", "Orion, what's the status?", "Orion, open Discord.",
    "Orion, stop the server.", "The quick brown fox jumps over the lazy dog.",
    "Orion, show me the server logs.", "I'm going to take a short break now.", "Orion, restart the server.",
    "Today I want to finish the new quest line.", "Orion, what's waiting for my approval?",
    "Bright stars over a quiet desert road.", "Orion, open VS Code.", "Remind me to drink some water.",
    "Orion, I'm done for the day.", "Seven purple dragons guard the northern gate.", "Orion, start streaming.",
    "Let's check the logs before we update.", "Orion, what time is it?", "My voice is the only one you answer.",
    "Orion, good morning.",
]


def frame_db(frame: np.ndarray) -> float:
    """Loudness of one frame in dBFS (0 = the loudest a microphone can record)."""
    x = frame.astype(np.float32) / 32768.0
    rms = math.sqrt(float(np.mean(x * x))) if x.size else 0.0
    return 20 * math.log10(rms) if rms > 1e-6 else -120.0


def strip_wake(text: str, required: bool) -> Optional[str]:
    """'Orion, start the server' -> 'start the server'. None if the wake word is required and missing."""
    m = WAKE_RE.match(text or "")
    if m:
        rest = text[m.end():].strip()
        return rest or None
    return None if required else (text.strip() or None)


def cosine(a: np.ndarray, b: np.ndarray) -> float:
    na, nb = float(np.linalg.norm(a)), float(np.linalg.norm(b))
    return float(np.dot(a, b) / (na * nb)) if na and nb else 0.0


class Segmenter:
    """Turns a stream of 30 ms int16 frames into phrases, using a loudness gate and a speech detector."""

    def __init__(self, is_speech: Callable[[np.ndarray], bool], min_db: float, max_db: float) -> None:
        self.is_speech, self.min_db, self.max_db = is_speech, min_db, max_db
        self.preroll: collections.deque = collections.deque(maxlen=int(PREROLL_S * RATE / FRAME))
        self.active: list[np.ndarray] = []
        self.run = 0
        self.quiet = 0

    def feed(self, frame: np.ndarray) -> Optional[np.ndarray]:
        db = frame_db(frame)
        voiced = self.min_db <= db <= self.max_db and self.is_speech(frame)
        if not self.active:
            self.preroll.append(frame)
            self.run = self.run + 1 if voiced else 0
            if self.run >= START_FRAMES:
                self.active = list(self.preroll)[-(START_FRAMES + 10):]   # a little lead-in, not 2 s of room noise
                self.preroll.clear()
                self.quiet = 0
            return None
        self.active.append(frame)
        self.quiet = 0 if voiced else self.quiet + 1
        too_long = len(self.active) * FRAME / RATE >= MAX_UTTERANCE_S
        if self.quiet >= END_SILENCE_FRAMES or too_long:
            audio = np.concatenate(self.active[: len(self.active) - (0 if too_long else self.quiet)])
            self.active, self.run, self.quiet = [], 0, 0
            if len(audio) / RATE >= MIN_UTTERANCE_S:
                return audio
        return None


class VoiceProfile:
    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self) -> Optional[dict[str, Any]]:
        try:
            d = json.loads(self.path.read_text())
            d["embedding"] = np.asarray(d["embedding"], dtype=np.float32)
            return d
        except (OSError, ValueError, KeyError):
            return None

    def save(self, embeddings: list[np.ndarray], model: str) -> dict[str, Any]:
        """Average the enrollment phrases; the threshold sits a bit under how well they match each other."""
        e = np.stack([x / (np.linalg.norm(x) or 1) for x in embeddings])
        mean = e.mean(axis=0)
        sims = sorted(cosine(x, mean) for x in e)
        threshold = max(0.55, min(0.8, sims[max(0, len(sims) // 10)] - 0.08))
        d = {"embedding": mean.tolist(), "threshold": round(threshold, 3), "model": model,
             "phrases": len(embeddings), "created": time.time()}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(d))
        os.chmod(self.path, 0o600)
        return d


class SpeechBrainEmbedder:
    """Speaker embeddings from SpeechBrain's ECAPA model (downloaded once into the data folder)."""
    name = "speechbrain/spkrec-ecapa-voxceleb"

    def __init__(self, models_dir: Path) -> None:
        from speechbrain.inference.speaker import EncoderClassifier  # voice add-on
        self.model = EncoderClassifier.from_hparams(source=self.name, savedir=str(models_dir / "ecapa"),
                                                    run_opts={"device": "cpu"})

    def __call__(self, audio_i16: np.ndarray) -> np.ndarray:
        import torch
        wav = torch.from_numpy(audio_i16.astype(np.float32) / 32768.0).unsqueeze(0)
        return self.model.encode_batch(wav).squeeze().detach().cpu().numpy()


class HotMic:
    """Ties it together. `handle(text)` is daemon.converse; `speak(text)` says the reply on this machine."""

    def __init__(self, data_dir: Path, handle: Callable[[str], Any], speak: Callable[[str], Any],
                 publish: Callable[[dict[str, Any]], Any] = lambda e: None,
                 transcribe: Optional[Callable[[np.ndarray], str]] = None,
                 embed: Optional[Callable[[np.ndarray], np.ndarray]] = None,
                 is_speech: Optional[Callable[[np.ndarray], bool]] = None,
                 wake_detector: Optional[Callable[[np.ndarray], float]] = None) -> None:
        self.data_dir = Path(data_dir)
        self.cfg_path = self.data_dir / "hotmic.json"
        self.profile = VoiceProfile(self.data_dir / "voice_profile.json")
        self.handle, self.speak, self.publish = handle, speak, publish
        self._transcribe, self._embed, self._is_speech = transcribe, embed, is_speech
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self.last: dict[str, Any] = {}
        self._wake_detector, self.wake_why = wake_detector, "off"   # [v1gaps] wake word (wake_word.py)

    # ------------------------------------------------------------------ settings
    def config(self) -> dict[str, Any]:
        try:
            return {**DEFAULTS, **json.loads(self.cfg_path.read_text())}
        except (OSError, ValueError):
            return dict(DEFAULTS)

    def set_config(self, **kv: Any) -> dict[str, Any]:
        cfg = {**self.config(), **{k: v for k, v in kv.items() if k in DEFAULTS and v is not None}}
        self.cfg_path.parent.mkdir(parents=True, exist_ok=True)
        self.cfg_path.write_text(json.dumps(cfg))
        return cfg

    def status(self) -> dict[str, Any]:
        p = self.profile.load()
        return {**self.config(), "listening": self.listening, "profile": bool(p),
                "threshold": p["threshold"] if p else None, "last": self.last,
                "ready": self.missing() == [],
                "wake": {"on": bool(self.config().get("wake_word")), "active": self.wake_why == "",  # [v1gaps]
                         "why": self.wake_why}}

    @property
    def listening(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def missing(self) -> list[str]:
        out = []
        for mod, what in (("sounddevice", "audio input"), ("webrtcvad", "speech detector"),
                          ("faster_whisper", "Whisper"), ("speechbrain", "speaker check")):
            try:
                __import__(mod)
            except Exception:
                out.append(what)
        if not self.profile.load():
            out.append("your voice profile (orion-ctl voice enroll)")
        return out

    # ------------------------------------------------------------------ the decision for one phrase
    def accept(self, audio: np.ndarray, woke: bool = False) -> Optional[str]:
        """Returns the command text if this phrase is you and is a command, else None (and says why in self.last)."""
        prof = self.profile.load()
        if prof is None:
            self.last = {"ignored": "no voice profile"}
            return None
        emb = self._embedder()(audio)
        sim = cosine(emb, prof["embedding"])
        if sim < prof["threshold"]:
            self.last = {"ignored": "not your voice", "similarity": round(sim, 3)}
            return None
        text = self._transcriber()(audio)
        cmd = strip_wake(text, self.config()["require_wake"] and not woke)   # [v1gaps] heard "Orion" already
        if not cmd:
            self.last = {"ignored": "no wake word" if text else "no words", "heard": text[:120]}
            return None
        self.last = {"heard": text[:200], "similarity": round(sim, 3), "at": time.time()}
        return cmd

    # ------------------------------------------------------------------ lazy models
    def _embedder(self):
        if self._embed is None:
            self._embed = SpeechBrainEmbedder(self.data_dir / "models")
        return self._embed

    def _transcriber(self):
        if self._transcribe is None:
            from faster_whisper import WhisperModel
            model = WhisperModel(os.getenv("ORION_WHISPER_MODEL", "base.en"), device="cpu", compute_type="int8")

            def run(audio: np.ndarray) -> str:
                segs, _ = model.transcribe(audio.astype(np.float32) / 32768.0, beam_size=1, language="en")
                return " ".join(s.text.strip() for s in segs).strip()
            self._transcribe = run
        return self._transcribe

    def _speech(self):
        if self._is_speech is None:
            import webrtcvad
            vad = webrtcvad.Vad(2)
            self._is_speech = lambda f: vad.is_speech(f.astype(np.int16).tobytes(), RATE)
        return self._is_speech

    # ------------------------------------------------------------------ on / off
    def start(self) -> dict[str, Any]:
        miss = self.missing()
        if miss:
            raise RuntimeError("Hot mic needs: " + ", ".join(miss))
        self.set_config(enabled=True)
        if not self.listening:
            self._stop.clear()
            self._thread = threading.Thread(target=self._loop, name="hotmic", daemon=True)
            self._thread.start()
        self.publish({"event": "HOTMIC_STATE", "on": True})
        return self.status()

    def stop(self, remember: bool = True) -> dict[str, Any]:
        if remember:
            self.set_config(enabled=False)
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)
        self._thread = None
        self.publish({"event": "HOTMIC_STATE", "on": False})
        return self.status()

    def process_frames(self, frames, seg: Optional[Segmenter] = None) -> list[str]:
        """Feed frames through the whole pipeline (used by the live loop and by tests). Returns commands run."""
        cfg = self.config()
        seg = seg or Segmenter(self._speech(), cfg["min_db"], cfg["max_db"])
        import wake_word  # [v1gaps] openWakeWord gate: Whisper and the voice check only run after "Orion"
        gate, self.wake_why = wake_word.make_gate(self.data_dir, cfg, self._wake_detector, on_wake=lambda sc: (
            self.last.update(wake_at=time.time()), self.publish({"event": "HOTMIC_WAKE", "score": round(sc, 2)})))
        ran = []
        for f in frames:
            if gate is not None:
                gate.feed(f)
            audio = seg.feed(f)
            if audio is None:
                continue
            if gate is not None and not gate.armed():
                self.last = {"ignored": "no wake word (not heard)"}
                continue
            try:
                cmd = self.accept(audio, woke=gate is not None)
                if gate is not None:
                    gate.disarm()
            except Exception:
                log.exception("hot mic phrase failed")
                continue
            if cmd:
                ran.append(cmd)
                self.publish({"event": "HOTMIC_HEARD", "text": cmd})
                reply = self.handle(cmd)
                if reply:
                    self.speak(reply)
        return ran

    def _loop(self) -> None:
        import queue

        import sounddevice as sd
        q: queue.Queue = queue.Queue(maxsize=200)

        def cb(indata, frames, _t, status):
            try:
                q.put_nowait(indata[:, 0].copy())
            except queue.Full:
                pass
        cfg = self.config()
        seg = Segmenter(self._speech(), cfg["min_db"], cfg["max_db"])

        def frames():
            while not self._stop.is_set():
                try:
                    yield q.get(timeout=0.5)
                except queue.Empty:
                    continue
        try:
            with sd.InputStream(samplerate=RATE, channels=1, dtype="int16", blocksize=FRAME, callback=cb):
                self.process_frames(frames(), seg)
        except Exception:
            log.exception("hot mic stopped")
            self.publish({"event": "HOTMIC_STATE", "on": False, "error": "microphone unavailable"})


# ---------------------------------------------------------------------- CLI: enroll and calibrate
def _record(seconds: float, until_quiet: bool = True) -> np.ndarray:
    import sounddevice as sd
    import webrtcvad
    vad = webrtcvad.Vad(2)
    out, quiet, heard = [], 0, False
    with sd.InputStream(samplerate=RATE, channels=1, dtype="int16", blocksize=FRAME) as s:
        for _ in range(int(seconds * RATE / FRAME)):
            f, _ = s.read(FRAME)
            f = f[:, 0]
            out.append(f)
            sp = vad.is_speech(f.tobytes(), RATE)
            heard |= sp
            quiet = 0 if sp else quiet + 1
            if until_quiet and heard and quiet >= END_SILENCE_FRAMES:
                break
    return np.concatenate(out)


def _cli(argv: list[str]) -> int:
    from config import settings
    data = Path(settings.data_dir)
    if not argv or argv[0] not in ("enroll", "calibrate"):
        print("usage: hotmic.py enroll | calibrate")
        return 2
    if argv[0] == "calibrate":
        input("Calibrating the hot mic. Stay quiet for 3 seconds. Press Enter to start…")
        room = _record(3, until_quiet=False)
        input("Now talk normally from where you usually sit for 4 seconds. Press Enter to start…")
        talk = _record(4, until_quiet=False)
        rdb = np.percentile([frame_db(room[i:i + FRAME]) for i in range(0, len(room) - FRAME, FRAME)], 90)
        tdb = [frame_db(talk[i:i + FRAME]) for i in range(0, len(talk) - FRAME, FRAME)]
        speech = np.percentile(tdb, 70)
        lo, hi = float(max(rdb + 4, speech - 18)), float(min(-1.0, max(tdb) + 8))
        if speech - rdb < 6:
            print(f"Your voice ({speech:.0f} dB) is barely louder than the room ({rdb:.0f} dB). Move closer or turn the "
                  "mic up in Settings > Sound, then try again.")
            return 1
        cfg = HotMic(data, lambda t: None, lambda t: None).set_config(min_db=round(lo, 1), max_db=round(hi, 1))
        print(f"Room {rdb:.0f} dB, your voice {speech:.0f} dB. Orion listens between {cfg['min_db']} and {cfg['max_db']} dB.")
        return 0
    print("Recording your voice profile: read each line aloud, at your normal volume. It takes about 2 minutes.")
    print("Downloading the speaker-check model the first time (about 80 MB)…")
    emb = SpeechBrainEmbedder(data / "models")
    vecs = []
    for i, line in enumerate(ENROLL_LINES, 1):
        input(f"\n[{i}/{len(ENROLL_LINES)}] Press Enter, then say:  {line}")
        audio = _record(6)
        if len(audio) / RATE < 0.8:
            print("  That was very short; let's skip it.")
            continue
        vecs.append(emb(audio))
        print("  Got it.")
    if len(vecs) < 12:
        print("Not enough clear recordings. Run it again somewhere quieter.")
        return 1
    d = VoiceProfile(data / "voice_profile.json").save(vecs, SpeechBrainEmbedder.name)
    print(f"\nSaved your voice profile ({d['phrases']} phrases, match threshold {d['threshold']}).")
    print("Turn the hot mic on with:  orion-ctl mic on   (Ctrl+Alt+M mutes it any time)")
    return 0


if __name__ == "__main__":
    import sys
    raise SystemExit(_cli(sys.argv[1:]))
