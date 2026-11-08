"""Settings > Security check-up: what's open on this computer (Exposure Map), its weekly check-up (Host Health),
known holes in your projects and the USB image (Supply-Chain Scan) and tested updates (Dependency Autopilot).
Logic lives in security_settings.py (tested without GTK)."""
from __future__ import annotations

from typing import Any

from gi.repository import Adw  # noqa: F401  (Gtk/Adw versions are pinned by pages.py)

from . import security_settings as SS
from .pages import Page


class SecurityPage(Page):
    key, title, icon, section = "security", "Security check-up", "security-high-symbolic", "System"
    blurb = ("What other devices can reach, a weekly check-up of this computer, known holes in your projects, and "
             "dependency updates that test themselves. Orion does this by itself and sends cards.")

    def load(self) -> Any:
        return SS.load(self.sys.r)

    def _now(self, label: str, what: str, toast: str) -> Any:
        return self.button(label, lambda: (self.win.toast(toast), self.win.act(lambda: SS.act(self.sys.r, what),
                                                                               after=self.refresh)))

    def fill(self, st: Any) -> None:
        if st is None:
            g = self.group()
            g.add(self.row("Orion's brain isn't answering", "Start it to see the security check-up.",
                           icon="dialog-information-symbolic"))
            return
        cfg = st.get("config") or {}
        eg = self.group("What's open on this computer",
                        "Who can reach each program: this computer only, your home network, Tailscale or the internet. "
                        "A new one becomes a card.", suffix=self._now("Check now", "exposure", "Checking…"))
        rows = SS.exposure_rows(st)
        if not rows:
            eg.add(self.row("Not checked yet", "Orion checks every hour.", icon="network-workgroup-symbolic"))
        for title, sub, pill, kind in rows:
            eg.add(self.row(title, sub, icon="network-workgroup-symbolic",
                            suffix=[self.pill(pill, kind)] if pill else []))
        for line in SS.recent_changes(st):
            eg.add(self.row(line, icon="dialog-information-symbolic"))
        others = SS.other_machines(st)
        if others:
            eg.add(self.row("Other machines", ", ".join(others) + " (ask: “what's open on …?”)",
                            icon="computer-symbolic"))
        self.switch(eg, "Scan my home network weekly",
                    "A gentle check of your own network only (the 100 most common ports). Needs nmap.",
                    bool((cfg.get("exposure") or {}).get("lan_scan")),
                    lambda on: SS.save(self.sys.r, "exposure", "lan_scan", on))

        title, sub, kind = SS.health_line(st)
        hg = self.group("Weekly check-up", "A score out of 100 and the three most useful fixes, every Sunday.",
                        suffix=self._now("Check now", "health", "Checking this computer…"))
        hg.add(self.row(title, sub, icon="security-high-symbolic", suffix=[self.pill("fine" if kind == "ok" else "check", kind)] if kind else []))
        for i, fix in enumerate(SS.health_fixes(st), 1):
            hg.add(self.row(f"{i}. {fix}", icon="emblem-system-symbolic"))

        sg = self.group("Known holes in your projects", "Checked every day; serious ones reach your phone at once.",
                        suffix=self._now("Check now", "supply", "Checking your projects…"))
        title, sub, kind = SS.supply_line(st)
        sg.add(self.row(title, sub, icon="package-x-generic-symbolic",
                        suffix=[self.pill("check" if kind == "warn" else "fine", kind)] if kind else []))
        title, sub, kind = SS.image_line(st)
        sg.add(self.row(f"The USB image: {title}", sub, icon="media-removable-symbolic"))

        dg = self.group("Dependency updates", "Weekly: updates are tried in a throwaway copy and the tests run. You "
                                              "get one card per project; approving makes a branch, never on main.",
                        suffix=self._now("Check now", "deps", "Testing updates…"))
        self.switch(dg, "Test updates weekly", "", bool((cfg.get("deps") or {}).get("enabled", True)),
                    lambda on: SS.save(self.sys.r, "deps", "enabled", on))
        for name, sub, kind in SS.deps_rows(st):
            dg.add(self.row(name, sub, icon="folder-symbolic",
                            suffix=[self.pill("ok" if kind == "ok" else "look", kind)] if kind else []))
        e = Adw.EntryRow(title="Projects (folders, comma separated; empty = every repo Orion may use)",
                         show_apply_button=True)
        e.set_text(", ".join((cfg.get("deps") or {}).get("projects") or []))
        e.connect("apply", lambda row: self.win.act(
            lambda: SS.save(self.sys.r, "deps", "projects", SS.projects_from_text(row.get_text())),
            after=self.refresh))
        dg.add(e)
