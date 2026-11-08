"""Keeping the local model warm: pings only when Ollama answers, backs off, respects battery and the setting."""
import asyncio

import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient

import ollama_warm
from ollama_warm import BACKOFF_MAX_S, OllamaWarmer, read_battery


class FakeOllama:
    def __init__(self, models=("llama3.1:8b",), up=True, loaded=True):
        self.models = list(models)
        self.up = up
        self.loaded = loaded
        self.requests = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append((request.method, request.url.path, request.content))
        if not self.up:
            raise httpx.ConnectError("refused", request=request)
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": m, "model": m} for m in self.models]})
        if request.url.path == "/api/generate":
            return httpx.Response(200, json={"model": "llama3.1:8b", "response": "", "done": True})
        if request.url.path == "/api/ps":
            return httpx.Response(200, json={"models": [{"name": m, "model": m} for m in self.models]
                                             if self.loaded else []})
        return httpx.Response(404)

    def factory(self):
        return lambda: httpx.AsyncClient(transport=httpx.MockTransport(self.handler))


def warmer(fake, battery=None, **kw):
    return OllamaWarmer(url="http://ollama", model="llama3.1:8b", enabled=kw.pop("enabled", True), interval_s=240,
                        keep_alive="15m", min_battery=30, client_factory=fake.factory(),
                        battery=battery or (lambda: None), **kw)


def test_ping_keeps_model_loaded():
    fake = FakeOllama()
    w = warmer(fake)
    wait = asyncio.run(w.tick())
    assert wait == 240
    gen = [r for r in fake.requests if r[1] == "/api/generate"]
    assert len(gen) == 1 and b'"keep_alive":"15m"' in gen[0][2].replace(b" ", b"") and b'"prompt":""' in \
        gen[0][2].replace(b" ", b"")
    st = w.status()
    assert st["loaded"] is True and st["last_ping"] and st["last_ok"] and not st["paused"] and st["reason"] is None


def test_backoff_when_unreachable_then_recovers():
    fake = FakeOllama(up=False)
    w = warmer(fake)
    waits = [asyncio.run(w.tick()) for _ in range(8)]
    assert waits[:4] == [30, 60, 120, 240]
    assert waits[-1] == BACKOFF_MAX_S and max(waits) == BACKOFF_MAX_S
    assert not any(r[1] == "/api/generate" for r in fake.requests)   # never pinged while unreachable
    st = w.status()
    assert st["paused"] and "isn't reachable" in st["reason"] and st["loaded"] is False
    fake.up = True
    assert asyncio.run(w.tick()) == 240
    assert w.failures == 0 and w.status()["reason"] is None


def test_battery_low_pauses_and_plugged_in_resumes():
    fake = FakeOllama()
    state = {"on_battery": True, "percent": 22}
    w = warmer(fake, battery=lambda: state)
    asyncio.run(w.tick())
    assert fake.requests == []
    assert "battery" in w.status()["reason"] and w.status()["paused"]
    state["percent"] = 80          # on battery but plenty left
    asyncio.run(w.tick())
    assert any(r[1] == "/api/generate" for r in fake.requests)
    fake.requests.clear()
    state.update(on_battery=False, percent=5)   # plugged in: battery level doesn't matter
    asyncio.run(w.tick())
    assert any(r[1] == "/api/generate" for r in fake.requests)


def test_disabled_and_missing_model():
    fake = FakeOllama()
    w = warmer(fake, enabled=False)
    asyncio.run(w.tick())
    assert fake.requests == [] and "Turned off" in w.status()["reason"]
    fake2 = FakeOllama(models=["phi3:mini"])
    w2 = warmer(fake2)
    asyncio.run(w2.tick())
    assert not any(r[1] == "/api/generate" for r in fake2.requests)
    assert "isn't downloaded" in w2.status()["reason"] and w2.status()["loaded"] is False


def test_not_loaded_reported_from_ps():
    fake = FakeOllama(loaded=False)
    w = warmer(fake)
    asyncio.run(w.tick())
    assert w.status()["loaded"] is False


def _supply(root, name, **files):
    d = root / name
    d.mkdir(parents=True)
    for k, v in files.items():
        (d / k).write_text(v + "\n")


def test_read_battery_linux_sysfs(tmp_path):
    _supply(tmp_path, "BAT0", type="Battery", capacity="25", status="Discharging")
    _supply(tmp_path, "AC", type="Mains", online="0")
    _supply(tmp_path, "hidpp_battery_0", type="Battery", scope="Device", capacity="5", status="Discharging")
    assert read_battery(tmp_path, platform="linux") == {"on_battery": True, "percent": 25}
    (tmp_path / "AC" / "online").write_text("1\n")
    assert read_battery(tmp_path, platform="linux") == {"on_battery": False, "percent": 25}


def test_read_battery_desktop_and_windows(tmp_path):
    _supply(tmp_path, "AC", type="Mains", online="1")
    assert read_battery(tmp_path, platform="linux") is None        # no battery: always warm
    assert read_battery(tmp_path, platform="win32") is None        # Windows: no check
    assert read_battery(tmp_path / "missing", platform="linux") is None


def test_status_route(monkeypatch):
    fake = FakeOllama()
    w = warmer(fake)
    monkeypatch.setattr(ollama_warm, "warmer", w)
    app = FastAPI()
    app.include_router(ollama_warm.router)
    with TestClient(app) as c:
        st = c.get("/ollama/warm").json()
    assert st["model"] == "llama3.1:8b" and "loaded" in st and "reason" in st and "last_ping" in st


def test_start_and_stop_loop():
    fake = FakeOllama()
    w = warmer(fake)

    async def go():
        w.start()
        await asyncio.sleep(0.05)
        running = w.status()["warming"]
        await w.stop()
        return running
    assert asyncio.run(go()) is True
    assert w.status()["warming"] is False and w.status()["loaded"] is True
