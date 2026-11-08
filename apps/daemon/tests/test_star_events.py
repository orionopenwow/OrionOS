"""POST /events/star passes a small star on to the HUD and nothing else."""

from fastapi import FastAPI
from fastapi.testclient import TestClient

from star_events import make_router


class _Bus:
    def __init__(self):
        self.events = []

    async def publish(self, event, *, audience="ui", record=True):
        self.events.append((event, record))


class _Daemon:
    def __init__(self):
        self.bus = _Bus()


def _client():
    d = _Daemon()
    app = FastAPI()
    app.include_router(make_router(lambda: d))
    return TestClient(app), d


def test_star_is_published_not_recorded():
    c, d = _client()
    r = c.post("/events/star", json={"source": "terminal", "ok": False, "seconds": 74, "title": "cargo build"})
    assert r.status_code == 200 and r.json() == {"ok": True}
    assert d.bus.events == [({"event": "STAR", "source": "terminal", "ok": False, "seconds": 74,
                              "title": "cargo build"}, False)]


def test_star_rejects_oversized_fields():
    c, d = _client()
    assert c.post("/events/star", json={"title": "x" * 500}).status_code == 422
    assert c.post("/events/star", json={"seconds": -1}).status_code == 422
    assert d.bus.events == []


def test_defaults():
    c, d = _client()
    assert c.post("/events/star", json={}).status_code == 200
    assert d.bus.events[0][0]["source"] == "terminal"
