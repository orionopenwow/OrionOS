"""Settings page "Agent safety": Package Reality Check, Injection Firewall and Key Proxy (toolkit tools 7-9).
The talking-to-Orion part is in safety.py (no GTK, tested on its own)."""
from __future__ import annotations

from typing import Any

from gi.repository import Gdk, GLib

from .pages import Page
from .safety import AGENTS, Safety, block_line, token_line


class AgentSafetyPage(Page):
    key, title, icon, section = "agent-safety", "Agent safety", "security-medium-symbolic", "Orion"
    blurb = "Keeps your coding agents honest: real packages only, no orders from web pages, and no real keys in their hands."

    def load(self) -> Any:
        s = Safety(self.sys.r)
        st = s.status()
        return {"status": st, "usage": s.usage_today() if st else ""}

    def fill(self, d: dict[str, Any]) -> None:
        s = Safety(self.sys.r)
        st = d["status"]
        if st is None:
            g = self.group()
            g.add(self.row("Orion's brain isn't answering", "Start it to see and change these settings.",
                           icon="dialog-information-symbolic"))
            return
        pk, fw, keys = st.get("packages") or {}, st.get("firewall") or {}, st.get("keys") or {}

        g = self.group("Package Reality Check",
                       "When an agent adds a package, Orion checks it's real, not brand new, used by people, and not "
                       "a look-alike of a popular one. Suspicious ones wait for your OK in the Queue.")
        self.switch(g, "Check new packages", "On install commands, Proof Gate and every commit",
                    bool(pk.get("enabled", True)), lambda on: s.set_packages(enabled=on))
        self.combo(g, "When the package sites can't be reached", "Orion can't check a package while offline",
                   [("ask", "Ask me first"), ("allow", "Allow it")], pk.get("offline", "ask"),
                   lambda v: s.set_packages(offline=v))
        for r in (pk.get("recent") or [])[:3]:
            names = ", ".join(p.split("=")[0] for p in (r.get("packages") or [])[:4])
            g.add(self.row(f"Checked {names}", f"{r.get('origin', '')}: {r.get('verdict')}",
                           icon="package-x-generic-symbolic"))

        fg = self.group("Injection Firewall",
                        "Text agents read from outside (web pages, issues, emails, transcripts) is data, not orders. "
                        "If an agent tries something risky that copies that text, Orion stops it and shows you the "
                        "source.")
        self.switch(fg, "Stop agents following outside text", f"{fw.get('untrusted_snippets', 0)} pieces of outside "
                    "text on record right now", bool(fw.get("enabled", True)), s.set_firewall)
        blocks = fw.get("blocks") or []
        if not blocks:
            fg.add(self.row("Nothing stopped yet", "", icon="emblem-ok-symbolic"))
        for b in blocks[:5]:
            t, sub = block_line(b)
            fg.add(self.row(t, sub, icon="dialog-warning-symbolic"))

        kg = self.group("Key Proxy",
                        "Agents get a placeholder instead of your key. Orion adds the real key on the way out, only "
                        "for the sites and the spending cap you allow. Keys themselves are never shown here.")
        state = "running" if keys.get("running") else "stopped"
        kg.add(self.row("Key Proxy", f"{keys.get('address', '')} ({keys.get('error') or 'local only'})",
                        icon="network-server-symbolic",
                        suffix=[self.pill(state, "ok" if keys.get("running") else "warn")]))
        for p in keys.get("providers") or []:
            kg.add(self.row(p.get("label", p.get("name", "")), "Key set up in Orion" if p.get("key_set_up") else
                            "No key yet: add it during setup", icon="dialog-password-symbolic",
                            suffix=[self.pill("ready" if p.get("key_set_up") else "missing",
                                              "ok" if p.get("key_set_up") else "off")]))
        if d.get("usage"):
            kg.add(self.row("Today", d["usage"], icon="view-list-symbolic"))
        ng = self.group("Placeholders for your agents",
                        "Each one lasts a day and can spend up to $5. The settings are copied for you to paste.")
        for key, name, _p in AGENTS:
            ng.add(self.row(f"New placeholder for {name}", "", icon="list-add-symbolic",
                            suffix=[self.button("Copy settings", lambda k=key: self._new(s, k))]))
        for t in (keys.get("tokens") or [])[:10]:
            title, sub = token_line(t)
            sfx = [self.button("Revoke", lambda i=t["id"]: self.win.act(lambda: s.revoke(i), after=self.refresh),
                               style="destructive-action")] if t.get("state") == "active" else \
                [self.pill(t.get("state", ""), "off")]
            ng.add(self.row(title, sub, icon="avatar-default-symbolic", suffix=sfx))

    def _new(self, s: Safety, agent_key: str) -> None:
        def make() -> tuple[bool, str]:
            ok, msg, text = s.new_placeholder(agent_key)
            if ok and text:
                GLib.idle_add(self._copy, text)
            return ok, msg
        self.win.act(make, after=self.refresh)

    @staticmethod
    def _copy(text: str) -> bool:
        display = Gdk.Display.get_default()
        if display is not None:
            display.get_clipboard().set_content(Gdk.ContentProvider.new_for_value(text))
        return False
