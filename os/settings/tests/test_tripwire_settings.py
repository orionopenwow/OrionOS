"""Settings > Tripwires data (tripwire_settings.py): reads and writes the daemon's /tripwires routes."""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from orion_settings import tripwire_settings as T  # noqa: E402

STATE = {
    "config": {"callback": "http://127.0.0.1:9120", "baits": [{"kind": "passwords"}, {"kind": "cloud_key"}],
               "listener": {"enabled": False, "bind": "0.0.0.0", "port": 9135}, "repo": ""},
    "planted": [{"id": "a1b2c3", "kind": "passwords", "where": "~/Documents/passwords-old.txt", "hits": 0},
                {"id": "d4e5f6", "kind": "cloud_key", "where": "~/.config/cloud-backup/credentials", "hits": 2}],
    "hits": [{"at": 1760634000, "label": "a fake cloud key", "ip": "203.0.113.5", "user_agent": "aws-cli/2.15"}],
    "armed": True, "words": "2 tripwires set.",
}


class Runner:
    def __init__(self, answers):
        self.answers, self.sent = answers, []

    def http(self, method, url, body=None, timeout=4):
        path = url.rsplit(":9120", 1)[-1]
        self.sent.append((method, path, body))
        return self.answers.get((method, path))


def test_reads_state_in_plain_words():
    st = T.state(Runner({("GET", "/tripwires"): STATE}))
    assert T.bait_rows(st) == [("Old passwords file", "~/Documents/passwords-old.txt", "set"),
                               ("Fake cloud key", "~/.config/cloud-backup/credentials", "tripped 2x")]
    [(title, who)] = T.hit_rows(st)
    assert title.endswith("a fake cloud key") and who == "from 203.0.113.5 · aws-cli/2.15"
    assert T.chosen(st) == {"passwords", "cloud_key"}
    assert T.state(Runner({})) is None


def test_saving_and_asking():
    r = Runner({("POST", "/tripwires/config"): {"config": {}},
                ("POST", "/tripwires/plant"): {"ok": True, "message": "Asked your phone to set 2 tripwires."},
                ("POST", "/tripwires/remove"): {"message": "Removed 2 tripwires."}})
    assert T.set_kind(r, STATE, "desktop", True)[0]
    assert r.sent[-1][2] == {"baits": [{"kind": "passwords"}, {"kind": "cloud_key"}, {"kind": "desktop"}]}
    assert T.set_kind(r, STATE, "passwords", False)[0]
    assert r.sent[-1][2] == {"baits": [{"kind": "cloud_key"}]}
    ok, msg = T.save(r, {"callback": "ftp://somewhere"})
    assert not ok and "Orion's own address" in msg
    assert T.save(r, {"callback": "http://192.168.1.20:9135/"})[0] and r.sent[-1][2] == {
        "callback": "http://192.168.1.20:9135"}
    assert T.listener(r, True, 9135)[0] and r.sent[-1][2] == {"listener": {"enabled": True, "port": 9135}}
    assert T.plant(r, "the server") == (True, "Asked your phone to set 2 tripwires.")
    assert r.sent[-1] == ("POST", "/tripwires/plant", {"target": "the server"})
    assert T.remove(r) == (True, "Removed 2 tripwires.")
    assert T.plant(Runner({})) == (False, "Couldn't ask. Is Orion running?")
