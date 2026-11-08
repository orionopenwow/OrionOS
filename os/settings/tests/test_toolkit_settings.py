"""Settings, Raid night page data (toolkit_settings.py): reads and writes the daemon's /raid routes."""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from orion_settings import toolkit_settings as T  # noqa: E402

STATUS = {
    "config": {"discord_channel_id": "123456789012"}, "enabled": True, "schedule": "Fridays at 7 pm",
    "phase": "checklist", "next_start": "2026-10-16T19:00:00+02:00",
    "state": {"checklist": {"checks": [{"name": "server", "ok": True, "text": "Server is up"},
                                       {"name": "backup", "ok": None, "text": "Backups aren't set up yet"},
                                       {"name": "patch", "ok": False, "text": "Patch x isn't ready"}]},
              "log": [{"at": 1760634000, "text": "Raid started."}]},
}


class Runner:
    def __init__(self, answers):
        self.answers, self.sent = answers, []

    def http(self, method, url, body=None, timeout=4):
        self.sent.append((method, url.rsplit(":9120", 1)[-1], body))
        return self.answers.get((method, url.rsplit(":9120", 1)[-1]))


def test_reads_status_in_plain_words():
    st = T.raid(Runner({("GET", "/raid"): STATUS}))
    assert T.when_text(st) == "Fridays at 7 pm. Next one: Fri 16 Oct, 19:00."
    assert T.phase_pill(st) == ("checking", "warn")
    assert T.checks(st) == [("Server", "Server is up", "ok"), ("Last night's backup", "Backups aren't set up yet", "off"),
                            ("Staged patch", "Patch x isn't ready", "warn")]
    assert T.log_lines(st)[0].endswith("Raid started.")
    assert T.raid(Runner({})) is None
    assert T.when_text({"enabled": False}).startswith("Off")


def test_save_checks_before_sending():
    r = Runner({("POST", "/raid/config"): {"config": {}, "text": "Raid night: Fridays at 7 pm."}})
    assert T.save(r, {"discord_channel_id": "general"})[0] is False and r.sent == []
    assert T.save(r, {"schedule": "at 7"})[0] is False and r.sent == []
    ok, msg = T.save(r, {"schedule": "Fridays at 7 pm"})
    assert ok and msg.startswith("Raid night") and r.sent[-1] == ("POST", "/raid/config", {"schedule": "Fridays at 7 pm"})
    assert T.save(Runner({}), {"enabled": True}) == (False, T.save(Runner({}), {"enabled": True})[1])
    ok, msg = T.check_now(Runner({("POST", "/raid/check"): {"summary": "All good for raid night."}}))
    assert ok and "waiting for your OK" in msg
