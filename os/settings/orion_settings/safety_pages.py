"""Settings > Snapshots and Settings > Health (complete plan step 3, the safety net). Data: safety_data.py."""
from __future__ import annotations

from typing import Any

from gi.repository import Gtk  # noqa: E402

from . import safety_data as D
from .pages import Page


class SnapshotsPage(Page):
    key, title, icon, section = "snapshots", "Snapshots & undo", "document-revert-symbolic", "System"
    blurb = ("Orion takes a snapshot before every update and each night, so you can go back. Going back always "
             "asks for the admin password.")

    def load(self) -> Any:
        return {"snap": D.snapshots(self.sys.r, self.sys.root), "changes": D.changes(self.sys.r)}

    def fill(self, d: dict[str, Any]) -> None:
        s = d["snap"]
        g = self.group(s["title"] or "Snapshots", s["words"])
        if s["backend"] == "none":
            g.add(self.row("Not available here", "Install OrionOS with snapshots on (Install OrionOS to this PC), "
                           "or make the vault on the USB stick.", icon="dialog-information-symbolic"))
        else:
            if s["space"]:
                g.add(self.row("Space", s["space"], icon="drive-harddisk-symbolic"))
            g.add(self.row("Undo the last update", "Go back to just before it (asks for the admin password)",
                           icon="edit-undo-symbolic",
                           suffix=[self.terminal_button("Undo…", D.restore_command(), "destructive-action")]
                           if s["undo_to"] else [self.pill("nothing to undo")]))
            g.add(self.row("Take one now", "Before you try something risky", icon="camera-photo-symbolic",
                           suffix=[self.button("Take", lambda: (self.win.toast("Taking a snapshot…"),
                                                                self.win.act(lambda: D.take(self.sys.r),
                                                                             after=self.refresh)))]))
            lg = self.group("Snapshots", f"{s['count']} kept. The oldest are removed by themselves.")
            if not s["snapshots"]:
                lg.add(self.row("None yet", "The first one is taken tonight or before the next update."))
            for sn in s["snapshots"][:12]:
                sid = str(sn.get("id", ""))
                lg.add(self.row(f"{str(sn.get('date', ''))[:16]}  ·  {sn.get('kind_words', '')}",
                                str(sn.get("why") or ""), icon="document-open-recent-symbolic",
                                suffix=[self.terminal_button("Go back…", D.restore_command(sid))]))
        cg = self.group("What Orion changed lately", "One tap undoes a change; big ones ask your phone first.")
        if d["changes"] is None:
            cg.add(self.row("Orion's brain isn't answering", "Start it to see its change history."))
            return
        if not d["changes"]:
            cg.add(self.row("Nothing yet", ""))
        for c in d["changes"]:
            suffix: list[Gtk.Widget] = []
            if c.get("undone_by"):
                suffix = [self.pill("undone")]
            elif c.get("undo"):
                cid = str(c["id"])
                suffix = [self.button("Undo", lambda cid=cid: self.win.act(lambda: D.undo_change(self.sys.r, cid),
                                                                           after=self.refresh))]
            sub = " · ".join(x for x in (str(c.get("why") or ""), str(c.get("approved_by") or "")) if x)
            cg.add(self.row(str(c.get("what", "")), sub, icon="emblem-documents-symbolic", suffix=suffix))


class HealthPage(Page):
    key, title, icon, section = "health", "Health & kill switch", "emblem-system-symbolic", "Orion"
    blurb = "What Orion keeps checking, in plain words. The kill switch stops all self-changes, updates and agents."

    def load(self) -> Any:
        return {"health": D.health(self.sys.r), "snap": D.snapshots(self.sys.r, self.sys.root)}

    def fill(self, d: dict[str, Any]) -> None:
        kg = self.group("Kill switch")
        if d["snap"]["frozen"]:
            kg.add(self.row("Orion is frozen", "No self-changes, updates or agents. Unfreezing needs your phone.",
                            icon="weather-snow-symbolic",
                            suffix=[self.button("Ask my phone", lambda: self.win.act(lambda: D.ask_unfreeze(self.sys.r),
                                                                                     after=self.refresh))]))
        else:
            kg.add(self.row("Freeze Orion", "Stops self-changes, updates and agents right away. Also: say "
                            "“Orion, freeze”, or the button on your phone.", icon="weather-snow-symbolic",
                            suffix=[self.button("Freeze", lambda: self.win.act(lambda: D.freeze(self.sys.r),
                                                                               after=self.refresh),
                                                style="destructive-action")]))
        h = d["health"]
        g = self.group("Checks", h["words"] if h else "")
        if h is None:
            g.add(self.row("Orion's brain isn't answering", "Restart it from Settings > Orion.",
                           icon="dialog-warning-symbolic"))
            return
        for c in h["checks"]:
            st = str(c.get("status", "ok"))
            sub = str(c.get("reason", "")) + (f" {c['fix_words']}" if st != "ok" and c.get("fix_words") else "")
            g.add(self.row(str(c.get("label", c.get("id", ""))), sub, icon=D.STATUS_ICON.get(st),
                           suffix=[self.pill({"ok": "fine", "warn": "check", "fail": "problem"}.get(st, st),
                                             "ok" if st == "ok" else "warn")]))
