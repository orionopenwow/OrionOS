"""Settings > Security check-up page data (security_settings.py): reads /security/status, writes /security/config."""
from __future__ import annotations

import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from orion_settings import security_settings as SS  # noqa: E402

NOW = time.time()
STATUS = {
    "config": {"deps": {"enabled": True, "projects": []}, "exposure": {"lan_scan": False}},
    "machine": "orionos",
    "exposure": {"maps": {"orionos": {"listeners": [
        {"port": 3000, "proto": "tcp", "process": "node", "widest": "internet", "bind": "this computer only",
         "why": "shared to the whole internet with Tailscale Funnel"},
        {"port": 9121, "proto": "tcp", "process": "python3", "widest": "lan", "bind": "every address",
         "why": "firewall lets it through"},
        {"port": 9120, "proto": "tcp", "process": "python3", "widest": "local"}]},
        "GAMING-PC": {"listeners": []}},
        "changes": [{"at": NOW - 600, "machine": "orionos",
                     "changes": [{"kind": "wider", "row": {"port": 3000, "process": "node", "widest": "internet"}}]}]},
    "health": {"orionos": {"score": 64, "word": "fair", "at": NOW - 3600, "fixes": ["Turn the firewall on."]}},
    "supply": {"at": NOW - 7200, "tools": ["osv-scanner"], "count": 3, "serious": 1,
               "image": {"safe": False, "answer": "Not yet. The image has 1 serious hole.\n- x"}},
    "deps": {"running": None, "projects": ["/home/u/projects/openwow", "/home/u/projects/hud"],
             "runs": [{"project": "/home/u/projects/openwow", "at": NOW - 86400 * 2, "state": "ready",
                       "text": "9 updates ready, all tests pass, 1 needs a code change.\nReady:"}]},
}


class Runner:
    def __init__(self, answers):
        self.answers, self.sent = answers, []

    def http(self, method, url, body=None, timeout=4):
        path = url.rsplit(":9120", 1)[-1]
        self.sent.append((method, path, body))
        return self.answers.get((method, path))


def test_reads_everything_in_plain_words():
    st = SS.load(Runner({("GET", "/security/status"): STATUS}))
    assert SS.load(Runner({})) is None
    rows = SS.exposure_rows(st)
    assert rows[0] == ("node on port 3000/tcp", "Listens on this computer only; shared to the whole internet with "
                       "Tailscale Funnel", "internet", "warn")
    assert rows[1][2] == "home network" and rows[-1][0] == "1 more on this computer only"
    assert SS.other_machines(st) == ["GAMING-PC"]
    assert SS.recent_changes(st, now=NOW)[0] == "10 minutes ago on orionos: node port 3000 (now wider, internet)"
    assert SS.health_line(st) == ("64/100 (fair)", "Checked 1 hours ago", "warn")
    assert SS.health_fixes(st) == ["Turn the firewall on."]
    assert SS.supply_line(st) == ("3 known holes (1 serious)", "Checked 2 hours ago with osv-scanner", "warn")
    assert SS.image_line(st) == ("Not yet", "Not yet. The image has 1 serious hole.", "warn")
    assert SS.deps_rows(st, now=NOW) == [
        ("openwow", "2 days ago: 9 updates ready, all tests pass, 1 needs a code change.", "ok"),
        ("hud", "Not checked yet", "")]
    empty = {"config": {}, "machine": "x"}
    assert SS.health_line(empty)[0] == "Not checked yet" and SS.supply_line(empty)[0] == "Not checked yet"
    assert SS.image_line(empty)[0] == "Not scanned" and SS.exposure_rows(empty) == []


def test_saving_and_buttons():
    r = Runner({("POST", "/security/config"): {"ok": True, "config": {}},
                ("POST", "/deps/run"): {"ok": True, "started": ["openwow"]},
                ("POST", "/exposure/lan-scan"): {"error": "The home network scan is off."}})
    assert SS.save(r, "exposure", "lan_scan", True) == (True, "Saved.")
    assert r.sent[-1] == ("POST", "/security/config", {"exposure": {"lan_scan": True}})
    assert SS.projects_from_text(" ~/a, ~/b\n~/c ,") == ["~/a", "~/b", "~/c"]
    assert SS.act(r, "deps") == (True, "Testing updates; the result arrives as a card.")
    assert SS.act(r, "lan") == (False, "The home network scan is off.")
    assert SS.act(Runner({}), "health") == (False, "Orion isn't answering.")
    assert SS.save(Runner({}), "deps", "enabled", False)[0] is False
