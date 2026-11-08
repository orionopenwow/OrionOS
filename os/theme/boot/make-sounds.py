#!/usr/bin/env python3
"""Orion's boot sound, synthesised from scratch (no samples): a low swell that rises with the star fall, and a
soft chime when the photon ring ignites (2.6 s in, the same moment as the boot frames).

    python3 os/theme/boot/make-sounds.py      -> os/theme/sounds/boot.ogg, chime.ogg  (needs numpy + ffmpeg or sox)

Both are original works made by this script; licence: CC0 (see os/theme/sounds/LICENSE).
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import wave

import numpy as np

SR = 48000
IGNITE = 2.6
REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../.."))
OUT = os.path.join(REPO, "os/theme/sounds")


def env_exp(n: int, k: float) -> np.ndarray:
    return np.exp(-np.arange(n) / SR * k)


def swell(dur: float) -> np.ndarray:
    """A1 drone with fifth and octave; harmonics open up as it rises, a breath of noise under it."""
    t = np.arange(int(dur * SR)) / SR
    rise = np.clip(t / IGNITE, 0, 1) ** 2.2                       # rises toward ignition
    after = np.where(t > IGNITE, np.exp(-(t - IGNITE) * 1.6), 1.0)  # then lets go
    amp = rise * after
    out = np.zeros_like(t)
    rng = np.random.default_rng(3)
    for f, g in [(55.0, 1.0), (82.5, 0.55), (110.0, 0.45), (165.0, 0.18), (220.0, 0.12)]:
        bright = np.clip(t / IGNITE, 0, 1) ** (1 + f / 80)          # higher partials join later
        for det in (-0.6, 0.0, 0.6):                                 # a slow chorus
            ph = rng.uniform(0, 2 * np.pi)
            out += g * bright * np.sin(2 * np.pi * (f + det) * t + ph) / 3
    noise = rng.standard_normal(len(t))
    # one-pole low-pass whose cutoff rises with the swell (star fall: wind getting faster)
    y = np.zeros_like(noise)
    a1 = a2 = 0.0
    cut = 0.002 + 0.03 * np.clip(t / IGNITE, 0, 1) ** 2
    for i in range(len(noise)):      # two one-pole stages: a soft 12 dB/octave roll-off
        a1 += cut[i] * (noise[i] - a1)
        a2 += cut[i] * (a1 - a2)
        y[i] = a2
    y *= 2.5
    out += 2.2 * y * np.clip(t / IGNITE, 0, 1) ** 3 * after
    return out * amp


def bell(dur: float, f0: float, gain: float) -> np.ndarray:
    """A soft glassy bell: a few inharmonic partials with their own decays, a gentle attack."""
    n = int(dur * SR)
    t = np.arange(n) / SR
    out = np.zeros(n)
    for ratio, g, decay in [(1.0, 1.0, 1.4), (2.0, 0.35, 2.2), (2.76, 0.22, 3.0), (5.4, 0.08, 5.5), (8.93, 0.03, 8.0)]:
        out += g * np.sin(2 * np.pi * f0 * ratio * t) * env_exp(n, decay)
    attack = np.clip(t / 0.012, 0, 1)
    return out * attack * gain


def reverb(x: np.ndarray, seconds: float = 2.2, mix: float = 0.35) -> np.ndarray:
    """Convolution with decaying noise: a soft, wide room."""
    rng = np.random.default_rng(11)
    n = int(seconds * SR)
    ir = rng.standard_normal(n) * np.exp(-np.arange(n) / SR * 3.2)
    ir[: int(0.012 * SR)] = 0
    ir /= np.sqrt(np.sum(ir ** 2))
    size = 1 << int(np.ceil(np.log2(len(x) + n)))
    wet = np.fft.irfft(np.fft.rfft(x, size) * np.fft.rfft(ir, size), size)[: len(x) + n]
    dry = np.concatenate([x, np.zeros(n)])
    return dry * (1 - mix) + wet * mix


def chime() -> np.ndarray:
    total = 3.2
    out = np.zeros(int(total * SR))
    for at, f, g in [(0.0, 659.25, 0.55), (0.11, 987.77, 0.33), (0.24, 1318.5, 0.16)]:   # E5, B5, E6
        b = bell(total - at, f, g)
        i = int(at * SR)
        out[i:i + len(b)] += b[: len(out) - i]
    return out


def boot() -> np.ndarray:
    dur = IGNITE + 3.2
    out = swell(dur) * 0.55
    c = chime()
    i = int(IGNITE * SR)
    out[i:i + len(c)] += c[: len(out) - i]
    fade = int(0.4 * SR)
    out[-fade:] *= np.linspace(1, 0, fade) ** 2       # no click where the dry sound ends
    return out


def write(path: str, x: np.ndarray, peak_db: float = -6.0) -> None:
    x = x / (np.max(np.abs(x)) + 1e-9) * 10 ** (peak_db / 20)
    fade = int(0.25 * SR)
    x[-fade:] *= np.linspace(1, 0, fade)
    stereo = np.stack([x, x], axis=1)
    pcm = (np.clip(stereo, -1, 1) * 32767).astype("<i2")
    with tempfile.TemporaryDirectory() as tmp:
        wav = os.path.join(tmp, "s.wav")
        with wave.open(wav, "wb") as w:
            w.setnchannels(2)
            w.setsampwidth(2)
            w.setframerate(SR)
            w.writeframes(pcm.tobytes())
        if shutil.which("ffmpeg"):
            subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", wav, "-c:a", "libvorbis", "-q:a", "4",
                            "-metadata", "title=Orion boot", "-metadata", "copyright=CC0", path], check=True)
        else:
            subprocess.run(["sox", wav, "-C", "4", path], check=True)


def main() -> None:
    os.makedirs(OUT, exist_ok=True)
    write(os.path.join(OUT, "boot.ogg"), reverb(boot()))
    write(os.path.join(OUT, "chime.ogg"), reverb(chime()), peak_db=-8.0)
    print("written:", os.listdir(OUT))


if __name__ == "__main__":
    main()
