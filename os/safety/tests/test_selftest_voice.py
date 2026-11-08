"""orion-selftest's voice loopback: Orion says a phrase and Whisper must hear it; a tone when there's no voice or
Whisper; skipped cleanly without a sound device. Every tool is a fake.   python -m pytest -q os/safety/tests"""
from __future__ import annotations

import importlib.util
import sys
from importlib.machinery import SourceFileLoader
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SELFTEST = ROOT / "os/lab/bin/orion-selftest"


def load():
    loader = SourceFileLoader("orion_selftest_voice", str(SELFTEST))
    spec = importlib.util.spec_from_loader("orion_selftest_voice", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


def tool(d: Path, name: str, body: str) -> str:
    p = d / name
    p.write_text("#!/bin/sh\n" + body + "\n")
    p.chmod(0o755)
    return str(p)


def rig(tmp_path, monkeypatch, *, devices=True, heard="orion voice check seven blue stars over the quiet desert",
        tts=True, whisper=True):
    b = tmp_path / "bin"
    b.mkdir()
    play = tool(b, "pw-play", "exit 0")
    # the "microphone": writes a loud wav (the tone fallback can hear it)
    rec = tool(b, "pw-record", f'for a; do out="$a"; done; cp {tmp_path}/loud.wav "$out"; sleep 5')
    tool(b, "wpctl", "exit 0" if devices else "exit 1")
    st = load()
    st.tone(tmp_path / "loud.wav", seconds=0.2)
    monkeypatch.setenv("ORION_PW_PLAY", play)
    monkeypatch.setenv("ORION_PW_RECORD", rec)
    monkeypatch.setenv("ORION_WPCTL", str(b / "wpctl"))
    if tts:
        monkeypatch.setenv("ORION_TTS_CMD", 'head -c 4000 /dev/zero > "$2"')
    else:
        monkeypatch.setenv("ORION_TTS_CMD", "exit 1")
    # the daemon's voice_loopback.py, faked: "phrase" and "check FILE PHRASE"
    fake = tmp_path / "voice_loopback.py"
    if whisper:
        fake.write_text("import json,sys\nif sys.argv[1]=='phrase': print('Orion voice check.')\n"
                        f"else: print(json.dumps({{'ok': {heard!r} != '', 'skipped': False, 'heard': {heard!r}, 'score': 1}}))\n")
    else:
        fake.write_text("import json,sys\nif sys.argv[1]=='phrase': print('x')\n"
                        "else: print(json.dumps({'ok': True, 'skipped': True, 'heard': ''}))\n")
    monkeypatch.setenv("ORION_LOOPBACK", str(fake))
    monkeypatch.setenv("ORION_DAEMON_PYTHON", sys.executable)
    return st


def test_speech_round_trip_passes(tmp_path, monkeypatch):
    st = rig(tmp_path, monkeypatch)
    p = st.voice()
    assert p["ok"] and not p["skipped"] and p["advisory"] and "heard it back" in p["message"]


def test_speech_not_heard_is_an_advisory_note_not_a_failure(tmp_path, monkeypatch):
    st = rig(tmp_path, monkeypatch, heard="")
    p = st.voice()
    assert not p["ok"] and p["advisory"] and "heard \"nothing\"" in p["message"]
    run_ok = all(x["ok"] for x in [p] if not x["skipped"] and not x["advisory"])
    assert run_ok                      # advisory parts never fail the run


def test_no_whisper_or_no_voice_falls_back_to_the_tone(tmp_path, monkeypatch):
    st = rig(tmp_path, monkeypatch, whisper=False)
    p = st.voice()
    assert p["ok"] and "tone only" in p["message"]


def test_no_tts_voice_falls_back_to_the_tone(tmp_path, monkeypatch):
    st = rig(tmp_path, monkeypatch, tts=False)
    assert "tone only" in st.voice()["message"]


def test_skipped_cleanly_without_a_sound_device(tmp_path, monkeypatch):
    st = rig(tmp_path, monkeypatch, devices=False)
    p = st.voice()
    assert p["skipped"] and p["ok"] and "no microphone or speaker" in p["message"]
    monkeypatch.setenv("ORION_PW_PLAY", str(tmp_path / "missing"))
    assert st.voice()["skipped"]


def test_real_loopback_script_is_found_in_the_repo(monkeypatch):
    monkeypatch.delenv("ORION_LOOPBACK", raising=False)
    st = load()
    p = st.loopback_script()
    assert p is not None and p.name == "voice_loopback.py"
