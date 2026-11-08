"""Settings > Tripwires (Toolkit tool 22). The data side is tripwire_settings.py (tested without GTK)."""
from __future__ import annotations

from typing import Any

from gi.repository import Adw  # noqa: E402  (pages.py has already required Gtk 4 / Adw 1)

from . import tripwire_settings as T
from .pages import Page


class TripwiresPage(Page):
    key, title, icon, section = "tripwires", "Tripwires", "security-high-symbolic", "Orion"
    blurb = ("Bait that only an intruder or a rogue script would touch: fake passwords, a fake database login, a fake "
             "cloud key. If anything uses one, your phone gets an urgent alert saying where it came from.")

    def load(self) -> Any:
        return T.state(self.sys.r)

    def fill(self, st: Any) -> None:
        g = self.group("Status")
        if st is None:
            g.add(self.row("Orion's brain isn't answering", "Start it to set tripwires.",
                           icon="dialog-information-symbolic"))
            return
        g.add(self.row(st.get("words") or "", icon="security-high-symbolic",
                       suffix=[self.pill("armed", "ok") if st.get("armed") else self.pill("off")]))
        g.add(self.row("Set tripwires here", "Asks your phone first, then plants the bait below",
                       icon="emblem-ok-symbolic",
                       suffix=[self.button("Set", lambda: self.win.act(lambda: T.plant(self.sys.r),
                                                                       after=self.refresh))]))
        g.add(self.row("Set them on the server", "Use this on the Orion that runs on your server: it also starts the "
                       "listener there", icon="network-server-symbolic",
                       suffix=[self.button("Set", lambda: self.win.act(lambda: T.plant(self.sys.r, "the server"),
                                                                       after=self.refresh))]))
        if st.get("planted"):
            g.add(self.row("Remove all bait", "Only files Orion planted, and only if nobody changed them",
                           icon="user-trash-symbolic",
                           suffix=[self.button("Remove", lambda: self.win.act(lambda: T.remove(self.sys.r),
                                                                              after=self.refresh))]))
        rows = T.bait_rows(st)
        if rows:
            pg = self.group("Planted")
            for title, where, pill in rows:
                pg.add(self.row(title, where, icon="text-x-generic-symbolic",
                                suffix=[self.pill(pill, "warn" if pill.startswith("tripped") else "ok")]))
        hits = T.hit_rows(st)
        if hits:
            hg = self.group("Tripped", "Nothing real was exposed: these were bait.")
            for title, who in hits:
                hg.add(self.row(title, who, icon="dialog-warning-symbolic"))
        cfg = st.get("config") or {}
        bg = self.group("Which bait", "Never planted inside Orion's own public repo.")
        chosen = T.chosen(st)
        for kind in T.DEFAULT_KINDS:
            self.switch(bg, T.KIND_NAMES[kind], "", kind in chosen,
                        lambda on, kind=kind: T.set_kind(self.sys.r, st, kind, on))
        self.switch(bg, T.KIND_NAMES["git_branch"], "An old-looking branch in the repo below (your files untouched)",
                    "git_branch" in chosen, lambda on: T.set_kind(self.sys.r, st, "git_branch", on))
        r = Adw.EntryRow(title="Repo for the old branch", show_apply_button=True)
        r.set_text(str(cfg.get("repo") or ""))
        r.connect("apply", lambda row: self.win.act(lambda: T.save(self.sys.r, {"repo": row.get_text()}),
                                                    after=self.refresh))
        bg.add(r)
        ag = self.group("Where the alarm rings", "The bait links point here: Orion's own address, nothing else.")
        e = Adw.EntryRow(title="Orion's address in the bait", show_apply_button=True)
        e.set_text(str(cfg.get("callback") or ""))
        e.connect("apply", lambda row: self.win.act(lambda: T.save(self.sys.r, {"callback": row.get_text()}),
                                                    after=self.refresh))
        ag.add(e)
        lis = cfg.get("listener") or {}
        self.switch(ag, "Listener for the server", f"Answers on port {lis.get('port', 9135)} so bait on other "
                    "machines can reach this Orion", bool(lis.get("enabled")),
                    lambda on: T.listener(self.sys.r, on))
