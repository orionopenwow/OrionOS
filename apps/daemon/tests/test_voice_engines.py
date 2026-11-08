"""voice_engines.py: Kokoro stays the default, Voicebox is used when it runs on this computer (a stand-in Voicebox
on 127.0.0.1 here), Orion falls back to Kokoro when it doesn't, settings persist, VoiceDesign makes a profile,
cloned voices need consent, and only local Voicebox addresses are accepted."""
from __future__ import annotations

import asyncio
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import numpy as np
import pytest

import voice_engines as V


# ---------------------------------------------------------------- a stand-in Voicebox on 127.0.0.1
class FakeVoicebox:
    def __init__(self) -> None:
        self.requests: list[tuple[str, str, dict]] = []
        self.profiles = [
            {"id": "p-clone", "name": "My voice", "voice_type": "cloned", "default_engine": "qwen", "language": "en"},
            {"id": "p-design", "name": "Calm", "voice_type": "designed", "default_engine": "qwen_voice_design",
             "language": "en"},
        ]
        fake = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, code, obj=None, raw=None, ctype="application/json"):
                data = raw if raw is not None else json.dumps(obj).encode()
                self.send_response(code)
                self.send_header("content-type", ctype)
                self.send_header("content-length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                fake.requests.append(("GET", self.path, {}))
                if self.path == "/health":
                    return self._send(200, {"status": "healthy", "gpu_available": True, "gpu_type": "CUDA (test)"})
                if self.path == "/profiles":
                    return self._send(200, fake.profiles)
                if self.path == "/profiles/presets/qwen_custom_voice":
                    return self._send(200, {"engine": "qwen_custom_voice", "voices": [
                        {"voice_id": "Ryan", "name": "Ryan", "gender": "male", "language": "en"}]})
                return self._send(404, {"detail": "no"})

            def do_POST(self):
                n = int(self.headers.get("content-length") or 0)
                body = json.loads(self.rfile.read(n) or b"{}")
                fake.requests.append(("POST", self.path, body))
                if self.path == "/profiles":
                    p = {"id": f"p-{len(fake.profiles)}", **body}
                    fake.profiles.append(p)
                    return self._send(200, p)
                if self.path == "/v1/audio/speech":
                    tone = (0.3 * np.sin(np.linspace(0, 200, 4800))).astype(np.float32)
                    return self._send(200, raw=V.samples_to_wav(tone, 48000), ctype="audio/wav")
                return self._send(404, {"detail": "no"})

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


@pytest.fixture
def vb():
    f = FakeVoicebox()
    yield f
    f.close()


def free_url() -> str:
    """A local address where nothing listens (Voicebox not running)."""
    import socket
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return f"http://127.0.0.1:{port}"


class Bus:
    def __init__(self):
        self.events = []

    async def publish(self, ev, record=True):
        self.events.append(ev)


class Audio:
    def __init__(self):
        self.calls = []

    async def ensure_initialized(self):
        pass

    def _synthesize_array(self, text, voice, speed):
        self.calls.append(("synth", voice, speed))
        return np.zeros(2400, dtype=np.float32)

    async def speak_and_stream(self, text, bus, voice="af_nicole", speed=0.92):
        self.calls.append(("stream", voice, speed))
        return b""

    async def broadcast_waveform(self, samples, bus, sid):
        pass


class Daemon:
    def __init__(self):
        self.bus = Bus()
        self.audio_engine = Audio()

    async def transcribe(self, audio):
        return "how do I sound"


def run(c):
    return asyncio.run(c)


# ---------------------------------------------------------------- tests
def test_only_local_addresses():
    for ok in ("http://127.0.0.1:17493", "http://localhost:17493/", "http://[::1]:17493"):
        assert V.local_url(ok)
    for bad in ("http://192.168.1.5:17493", "https://127.0.0.1:17493", "http://example.com",
                "http://127.0.0.1.evil.com:17493", "http://user:pw@127.0.0.1:17493", "ftp://127.0.0.1"):
        with pytest.raises(V.LocalOnlyError):
            V.local_url(bad)
    with pytest.raises(V.LocalOnlyError):
        V.Voicebox("http://10.0.0.2:17493")


def test_remote_voicebox_env_is_refused(tmp_path, monkeypatch):
    monkeypatch.setenv("ORION_VOICEBOX_URL", "http://10.1.2.3:17493")
    ve = V.VoiceEngines(tmp_path)
    st = run(ve.engines())
    assert st["voicebox"]["reachable"] is False and "this computer" in st["voicebox"]["error"]


def test_kokoro_default_is_unchanged(tmp_path):
    ve, d = V.VoiceEngines(tmp_path), Daemon()
    assert ve.settings()["engine"] == "kokoro" and ve.settings()["voice"] == "af_nicole"
    run(ve.speak_and_stream(d, "hello"))
    assert d.audio_engine.calls == [("stream", "af_nicole", 0.92)]   # the same call Orion always made
    run(ve.speak_and_stream(d, "hello", voice="am_adam", speed=1.1))
    assert d.audio_engine.calls[-1] == ("stream", "am_adam", 1.1)
    run(ve.set({"voice": "af_sky", "speed": 1.2}))
    run(ve.speak_and_stream(d, "hello", voice="af_nicole", speed=0.92))   # the HUD's fixed defaults
    assert d.audio_engine.calls[-1] == ("stream", "af_sky", 1.2)           # mean: the chosen voice


def test_settings_persist_and_are_checked(tmp_path):
    ve = V.VoiceEngines(tmp_path)
    run(ve.set({"voice": "am_michael", "speed": 3, "warmth": 0.4, "chosen": True}))
    again = V.VoiceEngines(tmp_path).settings()
    assert again["voice"] == "am_michael" and again["speed"] == 1.5 and again["warmth"] == 0.4 and again["chosen"]
    run(ve.set({"voice": "not-a-voice"}))
    assert V.VoiceEngines(tmp_path).settings()["voice"] == "af_nicole"
    with pytest.raises(ValueError):
        run(ve.set({"engine": "cloud"}))


def test_voicebox_unreachable_falls_back_to_kokoro(tmp_path):
    ve, d = V.VoiceEngines(tmp_path, V.Voicebox(free_url())), Daemon()
    st = run(ve.engines())
    assert st["voicebox"]["reachable"] is False and st["voicebox"]["how"]
    with pytest.raises(RuntimeError):
        run(ve.set({"engine": "voicebox", "voice": "p-design"}))
    # settings that say voicebox (for example Voicebox was closed later) still speak, with Kokoro
    ve._save({**V.DEFAULTS, "engine": "voicebox", "voice": "p-design"})
    out = run(ve.speak_and_stream(d, "hello"))
    assert out["engine"] == "kokoro" and "isn't running" in out["note"]
    assert d.audio_engine.calls[0][0] == "synth"
    assert d.bus.events[0]["event"] == "AUDIO_PLAY" and d.bus.events[0]["engine"] == "kokoro"


def test_voicebox_reachable_lists_speaks_and_uses_delivery(tmp_path, vb):
    ve, d = V.VoiceEngines(tmp_path, V.Voicebox(vb.url)), Daemon()
    st = run(ve.engines())
    box = next(e for e in st["engines"] if e["id"] == "voicebox")
    assert box["available"] and st["voicebox"]["gpu"] == "CUDA (test)"
    ids = [v["id"] for v in box["voices"]]
    assert "p-design" in ids and "preset:qwen_custom_voice:Ryan" in ids
    run(ve.set({"engine": "voicebox", "voice": "p-design", "delivery": "warm and unhurried"}))
    out = run(ve.speak_and_stream(d, "hello there"))
    assert out == {"engine": "voicebox", "note": ""}
    speech = [b for m, p, b in vb.requests if p == "/v1/audio/speech"][-1]
    assert speech["voice"] == "p-design" and speech["response_format"] == "wav"
    assert speech["instructions"] == "warm and unhurried" and speech["model"] == "tts-1"
    ev = d.bus.events[-1]
    assert ev["event"] == "AUDIO_PLAY" and ev["sample_rate"] == 24000 and ev["engine"] == "voicebox"
    assert abs(ev["duration_s"] - 0.1) < 0.01      # 48 kHz from Voicebox, resampled to 24 kHz
    wav = run(ve.speak_wav(d, "hi"))
    assert wav[:4] == b"RIFF"


def test_preset_becomes_a_profile(tmp_path, vb):
    ve = V.VoiceEngines(tmp_path, V.Voicebox(vb.url))
    out = run(ve.set({"engine": "voicebox", "voice": "preset:qwen_custom_voice:Ryan"}))
    made = [b for m, p, b in vb.requests if m == "POST" and p == "/profiles"][-1]
    assert made["voice_type"] == "preset" and made["preset_engine"] == "qwen_custom_voice"
    assert made["preset_voice_id"] == "Ryan"
    assert out["active"]["voice"] == "p-2"


def test_cloned_voice_needs_consent(tmp_path, vb):
    ve = V.VoiceEngines(tmp_path, V.Voicebox(vb.url))
    with pytest.raises(PermissionError) as e:
        run(ve.set({"engine": "voicebox", "voice": "p-clone"}))
    assert V.CONSENT_LINE in str(e.value)
    assert run(ve.set({"engine": "voicebox", "voice": "p-clone", "consent": True}))["active"]["voice"] == "p-clone"


def test_design_makes_a_designed_profile(tmp_path, vb):
    ve = V.VoiceEngines(tmp_path, V.Voicebox(vb.url))
    with pytest.raises(ValueError):
        run(ve.design({"description": "low"}))
    out = run(ve.design({"description": "A calm, warm, low voice with a slight smile"}))
    made = [b for m, p, b in vb.requests if m == "POST" and p == "/profiles"][-1]
    assert made["voice_type"] == "designed" and made["design_prompt"].startswith("A calm")
    assert made["default_engine"] == "qwen_voice_design"
    assert out["ok"] and out["active"]["engine"] == "voicebox" and out["active"]["voice"] == out["voice"]["id"]


def test_design_without_voicebox(tmp_path):
    ve = V.VoiceEngines(tmp_path, V.Voicebox(free_url()))
    with pytest.raises(RuntimeError):
        run(ve.design({"description": "A calm, warm, low voice"}))


def test_audio_helpers():
    tone = (0.5 * np.sin(np.linspace(0, 100, 2400))).astype(np.float32)
    s, r = V.wav_to_samples(V.samples_to_wav(tone, 24000))
    assert r == 24000 and np.allclose(s, tone, atol=1e-3)
    assert V.resample(tone, 48000).size == 1200
    assert V.warm(tone, 0) is tone
    w = V.warm(tone, 1.0)
    assert w.shape == tone.shape and float(np.max(np.abs(w))) <= 1.0


def test_routes(tmp_path, vb, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    d = Daemon()
    d.voice_engines = V.VoiceEngines(tmp_path, V.Voicebox(vb.url))
    app = FastAPI()
    app.include_router(V.make_router(lambda: d))
    c = TestClient(app)
    paths = {rt.path for rt in V.make_router(lambda: d).routes}
    assert {"/voice/engines", "/voice/set", "/voice/preview", "/voice/design", "/voice/hear"} <= paths
    assert c.get("/voice/engines").json()["voicebox"]["reachable"] is True
    assert c.post("/voice/set", json={"engine": "voicebox", "voice": "p-clone"}).status_code == 403
    assert c.post("/voice/set", json={"engine": "nope"}).status_code == 400
    j = c.post("/voice/preview", json={"engine": "voicebox", "voice": "p-design", "out": "wav"}).json()
    assert j["ok"] and j["engine"] == "voicebox" and j["audio_base64"]
    assert d.voice_engines.settings()["engine"] == "kokoro"      # preview never saves
    j = c.post("/voice/preview", json={"text": "hi"}).json()
    assert j["ok"] and "audio_base64" not in j and d.bus.events[-1]["event"] == "AUDIO_PLAY"
    j = c.post("/voice/design", json={"description": "A bright, friendly voice"}).json()
    assert j["ok"] and j["active"]["engine"] == "voicebox"
    assert c.post("/voice/hear", content=b"RIFF....").json()["text"] == "how do I sound"
    assert c.post("/voice/hear", content=b"").status_code == 400


def test_main_app_has_voice_routes(monkeypatch):
    import main
    from conftest import daemon_client
    monkeypatch.setattr(main.daemon, "voice_engines", V.VoiceEngines(main.settings.data_dir, V.Voicebox(free_url())),
                        raising=False)
    j = daemon_client(main.app).get("/voice/engines").json()
    assert j["active"]["engine"] == "kokoro" and j["voicebox"]["reachable"] is False


def test_preset_preview_and_pick_make_one_profile(tmp_path, vb):
    ve, d = V.VoiceEngines(tmp_path, V.Voicebox(vb.url)), Daemon()
    samples, rate, used = run(ve.synthesize(d, "hi", {"engine": "voicebox", "voice": "preset:qwen_custom_voice:Ryan"}))
    assert used == "voicebox"
    run(ve.set({"engine": "voicebox", "voice": "preset:qwen_custom_voice:Ryan"}))
    made = [b for m, p, b in vb.requests if m == "POST" and p == "/profiles"]
    assert len(made) == 1
    ids = [v["id"] for v in run(ve.voicebox_state(fresh=True))["voices"]]
    assert "preset:qwen_custom_voice:Ryan" not in ids          # shown once, as the profile it became
