"""Settings pages for Orion's Toolkit. Raid night: when it is, where the post goes, and the last check.
The data side is toolkit_settings.py (tested without GTK)."""
from __future__ import annotations

from typing import Any

from gi.repository import Adw  # noqa: E402  (pages.py has already required Gtk 4 / Adw 1)

from . import toolkit_settings as T
from .pages import Page


class RaidNightPage(Page):
    key, title, icon, section = "raid", "Raid night", "x-office-calendar-symbolic", "Orion"
    blurb = ("Two hours before each raid Orion checks the server, last night's backup and any staged patch, then "
             "drafts the Discord post. It's only posted when you say so on your phone.")

    def load(self) -> Any:
        return T.raid(self.sys.r)

    def fill(self, st: Any) -> None:
        g = self.group("When")
        if st is None:
            g.add(self.row("Orion's brain isn't answering", "Start it to set up raid night.",
                           icon="dialog-information-symbolic"))
            return
        text, kind = T.phase_pill(st)
        g.add(self.row("Raid night", T.when_text(st), icon="x-office-calendar-symbolic",
                       suffix=[self.pill(text, kind)] if text else []))
        self.switch(g, "Raid night autopilot", "Checks before each raid and drafts the announcement",
                    bool(st.get("enabled")), lambda on: T.save(self.sys.r, {"enabled": on}))
        e = Adw.EntryRow(title="Day and time (like “Fridays at 7 pm”)", show_apply_button=True)
        e.connect("apply", lambda row: self.win.act(lambda: T.save(self.sys.r, {"schedule": row.get_text().strip()}),
                                                    after=self.refresh))
        g.add(e)
        cfg = st.get("config") or {}
        pg = self.group("Discord post", "Every post says it was written with Orion's help. Posting always waits for "
                                        "your OK on your phone.")
        c = Adw.EntryRow(title="Channel id for announcements", show_apply_button=True)
        c.set_text(str(cfg.get("discord_channel_id") or ""))
        c.connect("apply", lambda row: self.win.act(
            lambda: T.save(self.sys.r, {"discord_channel_id": row.get_text().strip()}), after=self.refresh))
        pg.add(c)
        pg.add(self.row("Check now", "Run the checks and draft the post now", icon="emblem-ok-symbolic",
                        suffix=[self.button("Check", lambda: self.win.act(lambda: T.check_now(self.sys.r),
                                                                          after=self.refresh))]))
        rows = T.checks(st)
        if rows:
            cg = self.group("Last check")
            for title, text, kind in rows:
                cg.add(self.row(title, text, icon="emblem-system-symbolic",
                                suffix=[self.pill({"ok": "ok", "warn": "needs you", "off": "skipped"}[kind], kind)]))
        lines = T.log_lines(st)
        if lines:
            lg = self.group("Raid log")
            for line in lines:
                lg.add(self.row(line, icon="text-x-generic-symbolic"))
