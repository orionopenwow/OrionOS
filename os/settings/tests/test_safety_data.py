"""Settings > Snapshots & undo and Health & kill switch: the data side (no GTK needed)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from orion_settings import safety_data as D  # noqa: E402


class Stub:
    def __init__(self, answers=None, files=None):
        self.answers = answers or {}
        self.files = files or {}
        self.posts = []

    def http(self, method, url, body=None, timeout=4):
        if method == "POST":
            self.posts.append((url.replace(D.API, ""), body))
        return self.answers.get((method, url.replace(D.API, "")))

    def read(self, path):
        return self.files.get(path)


def test_snapshots_from_daemon_newest_first():
    r = Stub({("GET", "/snapshots"): {"backend": "btrfs", "words": "3 snapshots", "undo_to": "7",
                                      "space": {"words": "40 GB free"},
                                      "snapshots": [{"id": "5", "date": "2026-10-01 03:30"},
                                                    {"id": "7", "date": "2026-10-03 12:00"}]},
              ("GET", "/freeze"): {"freeze": {"frozen": True}}})
    s = D.snapshots(r)
    assert s["title"] == D.BACKEND_WORDS["btrfs"] and s["space"] == "40 GB free"
    assert [x["id"] for x in s["snapshots"]] == ["7", "5"] and s["undo_to"] == "7" and s["frozen"]


def test_snapshots_fall_back_to_the_index_then_to_plain_words():
    r = Stub(files={D.INDEX: json.dumps({"backend": "vault", "snapshots": [], "words": "0 copies"})})
    assert D.snapshots(r)["backend"] == "vault"
    s = D.snapshots(Stub())
    assert s["backend"] == "none" and "aren't set up" in s["words"] and not s["frozen"]


def test_health_sorts_problems_first_and_actions_post():
    r = Stub({("GET", "/health/status?refresh=1"): {"status": "warn", "words": "Mostly fine.", "checks": [
        {"id": "disk", "status": "ok"}, {"id": "battery", "status": "fail"}, {"id": "voice", "status": "warn"}]},
        ("POST", "/freeze"): {"freeze": {"frozen": True}},
        ("POST", "/freeze/unfreeze"): {"ok": True, "message": "Asked your phone."},
        ("POST", "/snapshots"): {"ok": True, "message": "Snapshot 8 taken."},
        ("POST", "/changes/chg-1/undo"): {"ok": True, "pending": True, "message": "Waiting for your approval."}})
    assert [c["id"] for c in D.health(r)["checks"]] == ["battery", "voice", "disk"]
    assert D.freeze(r) == (True, "Frozen. Unfreeze it from your phone.")
    assert D.ask_unfreeze(r) == (True, "Asked your phone.")
    assert D.take(r) == (True, "Snapshot 8 taken.")
    assert D.undo_change(r, "chg-1") == (True, "Waiting for your approval.")
    assert D.health(Stub()) is None and D.changes(Stub()) is None
    assert D.take(Stub())[0] is False


def test_restore_commands_always_go_through_orion_ctl_undo():
    assert D.restore_command() == "orion-ctl undo"
    assert D.restore_command("7") == "orion-ctl undo 7"
    assert D.restore_command("7", "/home/orion/my notes.txt") == "orion-ctl undo 7 --path '/home/orion/my notes.txt'"
