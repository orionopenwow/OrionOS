"""Settings > Privacy dashboard: the data side (no GTK)."""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from orion_settings import sensors_data as D  # noqa: E402


class Stub:
    def __init__(self, answers=None):
        self.answers = answers or {}
        self.posts = []

    def http(self, method, url, body=None, timeout=4):
        if method == "POST":
            self.posts.append((url.replace(D.API, ""), body))
        return self.answers.get((method, url.replace(D.API, "")))


def test_rows_come_from_orions_brain():
    r = Stub({("GET", "/privacy/dashboard"): {"words": "2 things are on: Hot mic, Camera.", "sensors": [
        {"id": "hotmic", "label": "Hot mic", "on": True, "available": True, "can_off": True},
        {"id": "telemetry", "label": "Usage data", "on": False, "available": True, "can_off": False},
        {"label": "no id"}]}})
    d = D.dashboard(r)
    assert d["brain"] and d["on"] == 1 and [s["id"] for s in d["sensors"]] == ["hotmic", "telemetry"]
    assert D.state_words(d["sensors"][0]) == ("on", "warn") and D.state_words(d["sensors"][1]) == ("off", "ok")
    assert D.state_words({"available": False}) == ("not here", "")


def test_without_the_brain_the_camera_still_shows():
    d = D.dashboard(Stub(), {"camera": True, "capture": False})
    assert not d["brain"] and d["on"] == 1 and d["sensors"][0]["id"] == "camera" and d["sensors"][0]["can_off"]
    assert "isn't answering" in d["words"]


def test_off_and_all_off_post_to_the_brain():
    r = Stub({("POST", "/privacy/off"): {"ok": True, "message": "Hot mic is off."},
              ("POST", "/privacy/off-all"): {"ok": True, "message": "Everything is off."}})
    assert D.turn_off(r, "hotmic") == (True, "Hot mic is off.")
    assert D.turn_all_off(r) == (True, "Everything is off.")
    assert r.posts == [("/privacy/off", {"id": "hotmic"}), ("/privacy/off-all", {})]
    assert D.turn_off(Stub(), "x") == (False, "Orion's brain isn't answering.")


def test_page_is_listed_after_privacy():
    src = (HERE.parent / "orion_settings" / "pages.py").read_text()
    assert "ALL.insert(ALL.index(PrivacyPage) + 1, SensorsPage)" in src
