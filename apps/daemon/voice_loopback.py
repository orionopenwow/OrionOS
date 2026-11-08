"""Voice loopback for the self-test (docs/ORIONOS_COMPLETE_PLAN.md Part 3, "Self-test suite ... a voice loopback test").

Orion says a short phrase out of the speaker, records the microphone at the same time, and checks that its own
speech-to-text (faster-whisper) hears the phrase. It proves the whole voice path end to end: text-to-speech, the
speaker, the microphone and Whisper.

Used by os/lab/bin/orion-selftest (which plays and records with PipeWire) through this file's command line, so it
runs in the daemon's own Python where Whisper is installed:

    python voice_loopback.py check  FILE.wav   ->  {"ok": true, "heard": "...", "score": 0.83}
    python voice_loopback.py phrase            ->  the phrase to say

No sound device, no TTS voice or no Whisper means "skipped", never "failed". The recording is a temporary file the
self-test deletes straight away; nothing is kept.
"""
from __future__ import annotations

import json
import os
import re
import sys
from typing import Any, Callable, Optional

PHRASE = "Orion voice check. Seven blue stars over the quiet desert."
PASS_SCORE = 0.5
STOP = {"the", "a", "an", "over", "of", "and", "to", "is"}
NUMBERS = {"7": "seven", "1": "one", "2": "two", "3": "three", "4": "four", "5": "five", "6": "six", "8": "eight",
           "9": "nine"}


def words(text: str) -> list[str]:
    out = []
    for w in re.findall(r"[a-z0-9']+", (text or "").lower()):
        w = NUMBERS.get(w, w)
        if w not in STOP:
            out.append(w)
    return out


def score(expected: str, heard: str) -> float:
    """Share of the phrase's words Whisper heard (0..1). "Orion" is often heard as "O'Ryan": it counts either way."""
    want = words(expected)
    got = set(words(heard))
    if not want:
        return 0.0
    hit = 0
    for w in want:
        if w in got or (w == "orion" and got & {"o'ryan", "oryan", "orien", "o'brien", "ryan", "orian"}):
            hit += 1
    return hit / len(want)


def check(path: str, expected: str = PHRASE,
          transcribe: Optional[Callable[[str], str]] = None) -> dict[str, Any]:
    if transcribe is None:
        try:
            from faster_whisper import WhisperModel  # voice add-on
        except Exception:
            return {"ok": True, "skipped": True, "why": "Whisper isn't installed", "heard": "", "score": 0.0}
        model = WhisperModel(os.getenv("ORION_WHISPER_MODEL", "base.en"), device="cpu", compute_type="int8")

        def transcribe(p: str) -> str:
            segs, _ = model.transcribe(p, beam_size=1, language="en", vad_filter=True)
            return " ".join(s.text.strip() for s in segs).strip()
    try:
        heard = transcribe(path)
    except Exception as exc:
        return {"ok": False, "skipped": False, "why": f"Whisper couldn't read the recording ({exc})"[:200],
                "heard": "", "score": 0.0}
    sc = round(score(expected, heard), 2)
    return {"ok": sc >= PASS_SCORE, "skipped": False, "heard": heard[:200], "score": sc,
            "why": "" if sc >= PASS_SCORE else "the words Orion heard don't match what it said"}


def main(argv: list[str]) -> int:
    if argv[:1] == ["phrase"]:
        print(PHRASE)
        return 0
    if len(argv) >= 2 and argv[0] == "check":
        res = check(argv[1], argv[2] if len(argv) > 2 else PHRASE)
        print(json.dumps(res))
        return 0 if res["ok"] else 1
    print("usage: voice_loopback.py check FILE.wav [PHRASE] | phrase", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
