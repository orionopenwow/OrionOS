"""Agent safety settings, the part without GTK: talks to Orion's daemon (toolkit tools 7, 8 and 9).

  * Package Reality Check: on/off, and what happens when the registries can't be reached (ask or allow).
  * Injection Firewall: on/off, and the calls it stopped recently.
  * Key Proxy: which real keys Orion holds (yes/no only, never the key), placeholders for agents, revoking them,
    and who used which key today.
"""
from __future__ import annotations

import time
from typing import Any, Optional

from .backend import API, Runner

AGENTS = [("claude_code", "Claude Code", ["anthropic"]), ("codex", "Codex", ["openai"]),
          ("gemini_cli", "Gemini CLI", ["gemini"])]


class Safety:
    def __init__(self, runner: Runner) -> None:
        self.r = runner

    def status(self) -> Optional[dict[str, Any]]:
        st = self.r.http("GET", f"{API}/agent-safety/status")
        return st if isinstance(st, dict) else None

    def set_packages(self, enabled: Optional[bool] = None, offline: Optional[str] = None) -> tuple[bool, str]:
        body: dict[str, Any] = {}
        if enabled is not None:
            body["enabled"] = enabled
        if offline is not None:
            body["offline"] = offline
        res = self.r.http("POST", f"{API}/package-check/settings", body)
        if not isinstance(res, dict) or "error" in res:
            return False, "Orion's brain isn't answering, so this couldn't be changed."
        return True, ("New packages are checked." if res.get("enabled") else "Package checks are off.") \
            if enabled is not None else ("Unchecked packages wait for your OK." if res.get("offline") == "ask"
                                         else "Unchecked packages are allowed.")

    def set_firewall(self, on: bool) -> tuple[bool, str]:
        res = self.r.http("POST", f"{API}/firewall/settings", {"enabled": on})
        if not isinstance(res, dict):
            return False, "Orion's brain isn't answering, so this couldn't be changed."
        return True, "The firewall is on." if res.get("enabled") else "The firewall is off. Agents can follow web pages."

    def new_placeholder(self, agent_key: str, cap_usd: float = 5.0, hours: float = 24.0) -> tuple[bool, str, str]:
        """(ok, message, settings text to paste into the agent's environment)."""
        name, providers = next(((n, p) for k, n, p in AGENTS if k == agent_key), (agent_key, ["openai"]))
        res = self.r.http("POST", f"{API}/key-proxy/tokens", {"agent": name, "mission": f"{name} (from Settings)",
                                                               "providers": providers, "cap_usd": cap_usd,
                                                               "ttl_hours": hours})
        if not isinstance(res, dict) or "token" not in res:
            err = (res or {}).get("error") if isinstance(res, dict) else None
            return False, err or "Orion's brain isn't answering, so no placeholder was made.", ""
        snip = (res.get("snippets") or {}).get(agent_key) or {}
        text = snip.get("env") or "\n".join(f"{k}={v}" for k, v in (res.get("env") or {}).items())
        note = snip.get("confidence", "")
        return True, f"Copied the settings for {name}. Paste them into its environment. {note}".strip(), text

    def revoke(self, token_id: str) -> tuple[bool, str]:
        res = self.r.http("POST", f"{API}/key-proxy/tokens/{token_id}/revoke", {})
        if isinstance(res, dict) and res.get("revoked"):
            return True, "Placeholder revoked. The agent using it can't reach your key any more."
        return False, "Couldn't revoke it (is Orion's brain running?)."

    def usage_today(self) -> str:
        res = self.r.http("GET", f"{API}/key-proxy/usage?since=today")
        return res.get("answer", "") if isinstance(res, dict) else ""


def token_line(t: dict[str, Any], now: Optional[float] = None) -> tuple[str, str]:
    """(title, subtitle) for one placeholder."""
    now = time.time() if now is None else now
    who = t.get("agent") or t.get("mission") or f"placeholder {t.get('id')}"
    left = max(0.0, float(t.get("expires") or 0) - now) / 3600
    sub = f"{', '.join(t.get('providers') or [])}; ${float(t.get('spent_usd') or 0):.2f} of " \
          f"${float(t.get('cap_usd') or 0):.2f} used; {t.get('calls', 0)} calls"
    state = t.get("state", "")
    if state == "active":
        sub += f"; {left:.0f} h left" if left >= 1 else "; less than an hour left"
    else:
        sub += f"; {state}"
    return who, sub


def block_line(b: dict[str, Any]) -> tuple[str, str]:
    m = (b.get("matches") or [{}])[0]
    when = time.strftime("%a %H:%M", time.localtime(float(b.get("at") or 0)))
    return f"{when}: stopped {b.get('tool', 'a call')}", (f"From {m.get('source', 'outside text')}: "
                                                         f"“{str(m.get('quote', ''))[:140]}”")
