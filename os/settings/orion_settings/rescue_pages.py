"""Settings page "Lock & rescue": when the screen locks and turns off, "Undo the last change", the Rescue panel, and
your own list of windows screen reading must never look at. The logic is in rescue_settings.py (no GTK)."""
from __future__ import annotations

from typing import Any

from gi.repository import Adw

from .pages import Page
from .rescue_settings import LOCK_CHOICES, OFF_CHOICES, LockRescue


class LockRescuePage(Page):
    key, title, icon, section = "lock-rescue", "Lock & rescue", "system-lock-screen-symbolic", "System"
    blurb = "When the screen locks, and how to get back when something goes wrong."

    def load(self) -> Any:
        lr = LockRescue()
        return {"idle": lr.idle(), "words": lr.exclude_words(), **lr.plans()}

    def fill(self, d: dict[str, Any]) -> None:
        lr = LockRescue()
        ug = self.group("Undo", "Put things back the way they were before Orion's last change or the last update.")
        for which, title in (("change", "Undo the last change"), ("update", "Undo the last update")):
            plan = d.get(which)
            if plan is None:
                ug.add(self.row(title, "Not available on this computer.", icon="edit-undo-symbolic"))
                continue
            sub = f"{plan.title}. {plan.detail}" if plan.possible else plan.detail
            btn = self.button("Undo", lambda p=plan, t=title: self.win.confirm(
                f"{t}?", f"{p.title}. {p.detail}", "Undo", lambda: self.win.act(lambda: lr.run(p), after=self.refresh)),
                sensitive=plan.possible)
            ug.add(self.row(title, sub, icon="edit-undo-symbolic", suffix=[btn]))
        ug.add(self.row("Rescue panel", "Terminal, files, browser, undo, a report for your phone. Also "
                        "Ctrl+Alt+Delete, and it opens by itself if the Orion screen keeps crashing.",
                        icon="help-browser-symbolic", suffix=[self.launch_button("Open", ["orion-rescue"])]))

        lg = self.group("Lock screen", "Your password unlocks it. It always locks before the computer sleeps.")
        idle = d["idle"]
        self.combo(lg, "Lock the screen", "When nobody has touched the computer", LOCK_CHOICES, idle["LOCK_AFTER"],
                   lambda v: lr.set_idle("LOCK_AFTER", v))
        self.combo(lg, "Turn the screens off", "Any key or touch turns them back on", OFF_CHOICES,
                   idle["SCREEN_OFF_AFTER"], lambda v: lr.set_idle("SCREEN_OFF_AFTER", v))
        lg.add(self.row("Lock now", "Super+L or Ctrl+Alt+L", icon="system-lock-screen-symbolic",
                        suffix=[self.launch_button("Lock", ["orion-lock"])]))

        sg = self.group("Screen reading: never look at",
                        "Orion never reads password managers, banking and payment sites, private browser windows, "
                        "login prompts or the lock screen. Add your own words: any window whose title contains one "
                        "is skipped too.")
        e = Adw.EntryRow(title="Words, separated by commas", show_apply_button=True)
        e.set_text(", ".join(d["words"]))
        e.connect("apply", lambda row: self.win.act(lambda: lr.set_exclude_words(row.get_text()), after=self.refresh))
        sg.add(e)
