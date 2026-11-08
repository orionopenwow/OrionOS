"""Settings > Backups: where backups go, when the last one ran, the monthly restore test, and the
"Try with Craft" default-app switches (Orion Studio). Logic lives in backups.py (tested without GTK)."""
from __future__ import annotations

from typing import Any

from gi.repository import Adw  # noqa: F401  (Gtk/Adw versions are pinned by pages.py)

from . import backups as BK
from .pages import Page


class BackupsPage(Page):
    key, title, icon, section = "backups", "Backups", "drive-harddisk-symbolic", "System"
    blurb = ("Encrypted copies of your projects, Orion and your home folder. Once a month Orion restores a few "
             "files to check the backups really work.")

    def load(self) -> Any:
        return BK.load(self.sys.r)

    def fill(self, st: Any) -> None:
        if st is None:
            g = self.group()
            g.add(self.row("Orion's brain isn't answering", "Start it to see and change backups.",
                           icon="dialog-information-symbolic"))
            return
        g = self.group("Status", BK.what_is_saved(st))
        for title, sub, kind in BK.rows(st):
            g.add(self.row(title, sub, icon="emblem-ok-symbolic" if kind == "ok" else "dialog-information-symbolic",
                           suffix=[self.pill({"ok": "fine", "warn": "check"}.get(kind, ""), kind)] if kind else []))
        g.add(self.row("Back up now", "", icon="document-save-symbolic",
                       suffix=[self.button("Back up", lambda: (self.win.toast("Backing up…"),
                                                               self.win.act(lambda: BK.back_up_now(self.sys.r),
                                                                            after=self.refresh)),
                                           style="suggested-action", sensitive=bool(st.get("configured")))]))
        g.add(self.row("Test a restore now", "Restores a few random files into a clean box and opens them",
                       icon="view-refresh-symbolic",
                       suffix=[self.button("Test", lambda: (self.win.toast("Testing a restore…"),
                                                            self.win.act(lambda: BK.drill_now(self.sys.r),
                                                                         after=self.refresh)),
                                           sensitive=bool(st.get("last_backup")))]))
        tg = self.group("Where backups go")
        usb = Adw.EntryRow(title="Folder on your USB drive (for example /media/you/Drive/orion-backup)",
                           show_apply_button=True)
        usb.set_text(st.get("usb") or "")
        usb.connect("apply", lambda row: self.win.act(lambda: BK.set_target(self.sys.r, usb=row.get_text()),
                                                      after=self.refresh))
        tg.add(usb)
        s3 = Adw.EntryRow(title="Cloud bucket, optional (s3:https://host/bucket/orion)", show_apply_button=True)
        s3.set_text(st.get("s3") or "")
        s3.connect("apply", lambda row: self.win.act(lambda: BK.set_target(self.sys.r, s3=row.get_text()),
                                                     after=self.refresh))
        tg.add(s3)
        if st.get("s3") and not st.get("s3_keys_set"):
            tg.add(self.row("The bucket's keys aren't set", "Put them in ~/.config/orion/daemon.env as "
                            "ORION_BACKUP_S3_KEY_ID and ORION_BACKUP_S3_SECRET. They're never shown here.",
                            icon="dialog-warning-symbolic"))
        rg = self.group("Getting files back")
        rg.add(self.row("How to restore", "In plain words, including on a new computer", icon="help-about-symbolic",
                        suffix=[self.terminal_button("Show", "orion-ctl backup recovery")]))
        rg.add(self.row("Restore the newest backup", "Into a new folder in ~/Restored; your files aren't touched",
                        icon="edit-undo-symbolic", suffix=[self.terminal_button("Restore", "orion-ctl backup restore")]))
        rg.add(self.row("Backup password", "Write it down or keep it in your vault: without it nobody can open the "
                        "backups", icon="dialog-password-symbolic",
                        suffix=[self.terminal_button("Show", "orion-ctl backup password")]))
        cg = self.group("Try with Craft", "Open some file types in the Orion Studio apps (alpha). Off unless you turn "
                                          "it on; the usual app stays in Open With.")
        for kind, info in (st.get("try_with_craft") or {}).items():
            sub = f"{info.get('label', kind)} open in {info.get('app', '')}"
            if not info.get("installed"):
                sub += f". {info.get('app', '')} isn't installed: get it in Get apps, Orion Studio."
            self.switch(cg, f"{info.get('label', kind)}: try with {info.get('app', '')}", sub, bool(info.get("on")),
                        lambda on, k=kind: BK.try_with_craft(self.sys.r, k, on),
                        sensitive=bool(info.get("installed") or info.get("on")))
