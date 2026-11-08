"""Hermetic test setup for the Orion daemon.

Run from apps/daemon:   .venv\\Scripts\\python -m pytest tests -q
Everything runs offline: no API keys, no Discord/Telegram, no microphone, no screen capture.
"""
import os
import sys
import tempfile
import types
from pathlib import Path

# Must happen before `config` is imported anywhere: explicit (even empty) values win over apps/daemon/.env.
_TMP = tempfile.mkdtemp(prefix="orion-tests-")
os.environ.update({
    "ORION_DATA_DIR": _TMP,
    "EMBEDDING_BACKEND": "hash",
    "ORION_CAPTURE_ENABLED": "false",
    "PROACTIVE_OBSERVER_ENABLED": "false",
    "OLLAMA_URL": "http://127.0.0.1:9",
    "OPENAI_API_KEY": "", "ANTHROPIC_API_KEY": "", "GEMINI_API_KEY": "", "GOOGLE_API_KEY": "",
    "DISCORD_TOKEN": "", "TELEGRAM_BOT_TOKEN": "", "TELEGRAM_API_ID": "0", "TELEGRAM_API_HASH": "",
    "SELF_EVOLVE_ENABLED": "true",
    "ORION_KEY_PROXY": "0",   # Key Proxy: tests start their own on a free port
    "ORION_TOKEN_FILE": os.path.join(_TMP, "daemon.token"),   # review B1: never touch the real runtime folder
})
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


class _AudioEngine:
    async def ensure_initialized(self):
        pass

    async def speak_and_stream(self, *a, **k):
        return b""


class _NoMic:
    def __init__(self, **k):
        raise RuntimeError("no microphone in tests")


class _TTS:
    is_available = False

    def __init__(self, **k):
        self.current_settings = {}

    def get_available_voices(self):
        return []


def _stub(name, **attrs):
    mod = types.ModuleType(name)
    mod.__dict__.update(attrs)
    sys.modules[name] = mod


_stub("audio_engine", audio_engine=_AudioEngine())
_stub("audio_stt", WhisperSTTEngine=_NoMic)
_stub("audio_tts", LocalTTS=_TTS)


# ------------------------------------------------------------------ review B1: talking to main.app in tests
LOCAL_BASE = "http://127.0.0.1:9120"


def auth_headers(extra=None):
    """Headers a legitimate local client sends: the per-start token."""
    import api_token
    return {"Authorization": f"Bearer {api_token.get()}", **(extra or {})}


def daemon_client(app, **kw):
    """A TestClient that looks like a local tool: Host 127.0.0.1 and the token on every request."""
    from fastapi.testclient import TestClient
    return TestClient(app, base_url=LOCAL_BASE, headers=auth_headers(), **kw)


def ws_url(path="/"):
    """Absolute WebSocket URL with the token (TestClient would otherwise use Host 'testserver')."""
    import api_token
    sep = "&" if "?" in path else "?"
    return f"ws://127.0.0.1:9120{path}{sep}token={api_token.get()}"


def pytest_configure(config):
    config.addinivalue_line("markers", "fingerprint_rule: test the brain-change fingerprint rule (self_program)")
