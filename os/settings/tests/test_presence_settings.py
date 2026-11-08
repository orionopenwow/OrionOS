"""Settings > Privacy > Camera presence without GTK: what it shows and what it sends to Orion's /presence routes."""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from orion_settings import presence_settings as PS  # noqa: E402
from orion_settings.backend import Runner  # noqa: E402


class HttpRunner(Runner):
    def __init__(self, answers):
        self.answers, self.sent = answers, []

    def http(self, method, url, body=None, timeout=4):
        self.sent.append((method, url.split("9120", 1)[-1], body))
        return self.answers.get((method, url.split("9120", 1)[-1]))


def test_not_running():
    assert PS.load(HttpRunner({})) is None
    assert "isn't answering" in PS.summary(None)
    assert PS.set_on(HttpRunner({}), True) == (False, PS.NOT_RUNNING)


def test_summary_in_plain_words():
    assert PS.summary({"on": False}).startswith("Off.")
    assert "pip install mediapipe" in PS.summary({"on": False, "missing": ["mediapipe"]})
    assert "12.5 min" in PS.summary({"on": True, "screen_time_today_min": 12.5})
    assert PS.summary({"on": True, "paused": "Camera resting"}) == "Camera resting"


def test_switches_hit_the_right_routes():
    r = HttpRunner({("POST", "/presence"): {"on": False, "error": "The camera is switched off in Settings."},
                    ("POST", "/presence/config"): {"config": {}}})
    ok, msg = PS.set_on(r, True)
    assert not ok and "switched off" in msg and r.sent[-1] == ("POST", "/presence", {"action": "on"})
    assert PS.set_option(r, "pause_media", True) == (True, "Saved.")
    assert r.sent[-1] == ("POST", "/presence/config", {"pause_media": True})
    assert PS.set_option(r, "camera_index; rm", True)[0] is False
    assert all(k in ("lock_when_away", "greet", "pause_media", "gestures", "off_on_battery", "start_with_orion")
               for k, _t, _s in PS.SWITCHES)
