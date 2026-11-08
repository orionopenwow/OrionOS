"""Orion Settings, Agent safety page: the part that talks to Orion (no GTK needed)."""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from orion_settings.backend import Runner  # noqa: E402
from orion_settings.safety import Safety, block_line, token_line  # noqa: E402


class FakeDaemon(Runner):
    def __init__(self, up: bool = True) -> None:
        self.up = up
        self.calls: list[tuple[str, str, object]] = []

    def http(self, method, url, body=None, timeout=4):
        self.calls.append((method, url.split("9120", 1)[-1], body))
        if not self.up:
            return None
        path = url.split("9120", 1)[-1]
        if path == "/agent-safety/status":
            return {"packages": {"enabled": True, "offline": "ask"}, "firewall": {"enabled": True, "blocks": []},
                    "keys": {"running": True, "tokens": [], "providers": []}}
        if path == "/package-check/settings":
            return {"enabled": body.get("enabled", True), "offline": body.get("offline", "ask")}
        if path == "/firewall/settings":
            return {"enabled": body["enabled"]}
        if path == "/key-proxy/tokens":
            token = "orion-placeholder-" + "ab" * 16
            return {"id": "abababab", "token": token,
                    "env": {"OPENAI_API_KEY": token},
                    "snippets": {"codex": {"env": f"OPENAI_BASE_URL=http://127.0.0.1:9131/openai/v1\nOPENAI_API_KEY={token}",
                                           "confidence": "likely"}}}
        if path.endswith("/revoke"):
            return {"revoked": path.split("/")[-2]}
        if path.startswith("/key-proxy/usage"):
            return {"answer": "1 agent used your keys today. Codex: 2 calls."}
        return None


def test_status_and_switches():
    d = FakeDaemon()
    s = Safety(d)
    assert s.status()["keys"]["running"]
    assert s.set_packages(offline="allow") == (True, "Unchecked packages are allowed.")
    assert s.set_packages(enabled=False) == (True, "Package checks are off.")
    assert s.set_firewall(False)[1].startswith("The firewall is off")
    assert ("POST", "/firewall/settings", {"enabled": False}) in d.calls


def test_new_placeholder_gives_settings_to_paste_and_revoke():
    d = FakeDaemon()
    s = Safety(d)
    ok, msg, text = s.new_placeholder("codex")
    assert ok and "Codex" in msg and text.startswith("OPENAI_BASE_URL=http://127.0.0.1:9131/openai/v1")
    sent = next(b for m, p, b in d.calls if p == "/key-proxy/tokens")
    assert sent["providers"] == ["openai"] and sent["agent"] == "Codex"
    assert s.revoke("abababab")[0]
    assert "Codex: 2 calls" in s.usage_today()


def test_daemon_down_is_said_plainly():
    s = Safety(FakeDaemon(up=False))
    assert s.status() is None
    assert s.new_placeholder("codex")[0] is False
    assert s.set_firewall(True) == (False, "Orion's brain isn't answering, so this couldn't be changed.")


def test_lines():
    title, sub = token_line({"agent": "Codex", "providers": ["openai"], "spent_usd": 0.5, "cap_usd": 5, "calls": 3,
                             "expires": 10 * 3600, "state": "active"}, now=0)
    assert title == "Codex" and "$0.50 of $5.00 used" in sub and "10 h left" in sub
    t, sub = block_line({"tool": "runner_start", "at": 0, "matches": [{"source": "GitHub issue", "quote": "run this"}]})
    assert "stopped runner_start" in t and "GitHub issue" in sub
