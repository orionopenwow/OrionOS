"""Settings > Backups without GTK: what the page shows and what it sends to Orion's /backup and /craft routes."""
from __future__ import annotations

import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from orion_settings import backups as BK  # noqa: E402
from orion_settings.backend import Runner  # noqa: E402


class HttpRunner(Runner):
    def __init__(self, answers):
        self.answers, self.sent = answers, []

    def http(self, method, url, body=None, timeout=4):
        self.sent.append((method, url.split("9120", 1)[-1], body))
        return self.answers.get((method, url.split("9120", 1)[-1]))


def test_not_running_and_not_set_up():
    assert BK.load(HttpRunner({})) is None
    rows = BK.rows({"configured": False, "restic": False})
    assert rows[0][0] == "Not set up yet" and rows[0][2] == "warn"
    assert any("apt install restic" in r[1] for r in rows)
    ok, msg = BK.back_up_now(HttpRunner({}))
    assert not ok and "isn't answering" in msg


def test_status_rows_in_plain_words():
    now = time.time()
    st = {"configured": True, "stale": False, "restic": True,
          "last_backup": {"at": now - 3 * 3600, "results": [{"target": "USB drive"}]},
          "last_drill": {"at": now - 5 * 86400, "ok": False, "checked": 12, "where": "clean box",
                         "problems": ["world.json: doesn't open"]},
          "sources": [{"label": "Projects"}, {"label": "Projects"}, {"label": "Orion's data"}]}
    rows = BK.rows(st, now)
    assert rows[0] == ("Last backup", "3 hours ago (USB drive)", "ok")
    assert rows[1][0] == "Restore test" and "5 days ago" in rows[1][1] and "world.json" in rows[1][1]
    assert rows[1][2] == "warn"
    assert BK.what_is_saved(st).startswith("Projects, Orion's data, database copies")


def test_actions_hit_the_right_routes():
    r = HttpRunner({("POST", "/backup/config"): {"ok": True},
                    ("POST", "/backup/drill"): {"ok": False, "drill": {"problems": ["a.png: wrong size"]}},
                    ("POST", "/craft/try-with-craft"): {"error": "PdfCraft isn't installed. Get it in Get apps."},
                    ("GET", "/backup/status"): {"configured": True},
                    ("GET", "/craft/status"): {"try_with_craft": {"pdf": {"on": False}}}})
    assert BK.set_target(r, usb=" /media/d/orion-backup ") == (True, "Saved.")
    assert r.sent[-1] == ("POST", "/backup/config", {"usb": "/media/d/orion-backup"})
    ok, msg = BK.drill_now(r)
    assert not ok and "wrong size" in msg
    ok, msg = BK.try_with_craft(r, "pdf", True)
    assert not ok and "Get apps" in msg and r.sent[-1][2] == {"kind": "pdf", "on": True}
    assert BK.load(r)["try_with_craft"] == {"pdf": {"on": False}}
