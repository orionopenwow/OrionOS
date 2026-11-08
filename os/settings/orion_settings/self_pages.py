"""Self-running pages (complete plan step 4): the permission levels become editable, and Updates gets the update
channel and the "test new versions in a VM first" switch. Data: self_data.py (no GTK, tested on its own)."""
from __future__ import annotations

from typing import Any

from gi.repository import Adw, Gtk  # noqa: E402

from . import self_data as D
from .pages import CORE, PermissionsPage, UpdatesPage


class SelfPermissionsPage(PermissionsPage):
    blurb = ("How far Orion may go on its own. Lowering a level works at once; raising one asks your phone or the "
             "admin password.")

    def load(self) -> Any:
        return {"levels": D.levels(self.sys.r), "learned": self.sys.autonomy()}

    def _pick(self, row: dict[str, Any], level: int) -> None:
        cur = int(row["level"])
        if level == cur:
            return
        if level < cur:
            self.win.act(lambda: D.set_level(self.sys.r, row["kind"], level), after=self.refresh)
            return
        dlg = Adw.MessageDialog(transient_for=self.win, heading="Let Orion do more on its own?",
                                body=f"“{row['label']}” would go to “{D.LEVEL_NAMES[level - 1]}”. Approve it on your "
                                     "phone, or type the orion-admin password in a terminal.")
        dlg.add_response("cancel", "Cancel")
        dlg.add_response("password", "Admin password")
        dlg.add_response("phone", "Ask my phone")
        dlg.set_response_appearance("phone", Adw.ResponseAppearance.SUGGESTED)

        def answered(_d: Adw.MessageDialog, resp: str) -> None:
            if resp == "phone":
                self.win.act(lambda: D.set_level(self.sys.r, row["kind"], level), after=self.refresh)
            elif resp == "password":
                ok = self.sys.terminal(D.admin_level_command(row["kind"], level) + "; echo; printf 'Press Enter. '; read _")
                self.win.toast("Type the orion-admin password in the terminal, then press refresh." if ok else
                               "Couldn't open a terminal window.")
        dlg.connect("response", answered)
        dlg.present()

    def fill(self, d: dict[str, Any]) -> None:
        lv = d["levels"]
        desc = ("Tap a dot to change a row. Rows with a lock can't be raised." if lv["live"] else
                "Orion's brain isn't answering, so these are the defaults and can't be changed right now.")
        if lv["frozen"]:
            desc = "Orion is frozen: nothing runs by itself until you unfreeze it. " + desc
        g = self.group("Changes to this computer and to Orion", desc)
        grid = Gtk.Grid(column_spacing=10, row_spacing=8, margin_top=10, margin_bottom=10, margin_start=14,
                        margin_end=14)
        grid.add_css_class("orion-levels")
        head = Gtk.Label(label="Kind of change", xalign=0, hexpand=True)
        head.add_css_class("head")
        grid.attach(head, 0, 0, 1, 1)
        for i, n in enumerate(D.LEVEL_NAMES):
            h = Gtk.Label(label=n)
            h.add_css_class("head")
            h.add_css_class("cell")
            grid.attach(h, i + 1, 0, 1, 1)
        y = 1
        for row in lv["rows"]:
            level = int(row["level"])
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
            k = Gtk.Label(label=row["label"], xalign=0, wrap=True)
            k.add_css_class("kind")
            box.append(k)
            bits = [str(row.get("note") or "")]
            if row.get("streak"):
                bits.append(f"{row['streak']} yeses in a row")
            if row.get("where") == "admin":
                bits.append("set with the admin password")
            n = Gtk.Label(label=". ".join(b for b in bits if b), xalign=0, wrap=True)
            n.add_css_class("note")
            box.append(n)
            grid.attach(box, 0, y, 1, 1)
            for i in range(3):
                lvl = i + 1
                if lvl > int(row["max"]):
                    lk = Gtk.Image.new_from_icon_name("changes-prevent-symbolic")
                    lk.add_css_class("orion-dim")
                    lk.set_tooltip_text("Can't go this far")
                    grid.attach(lk, i + 1, y, 1, 1)
                    continue
                dot = Gtk.Box(halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER)
                dot.add_css_class("orion-level-dot")
                if lvl == level:
                    dot.add_css_class("on")
                if lv["live"] and row.get("raisable"):
                    b = Gtk.Button(halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER)
                    b.add_css_class("flat")
                    b.set_child(dot)
                    b.set_tooltip_text(D.LEVEL_NAMES[i] + (" (current)" if lvl == level else ""))
                    b.connect("clicked", lambda _b, r=row, v=lvl: self._pick(r, v))
                    grid.attach(b, i + 1, y, 1, 1)
                else:
                    dot.set_tooltip_text(D.LEVEL_NAMES[i] + (" (current)" if lvl == level else ""))
                    grid.attach(dot, i + 1, y, 1, 1)
            y += 1
            grid.attach(Gtk.Separator(), 0, y, 4, 1)
            y += 1
        core = Gtk.Box(spacing=10, margin_top=4)
        core.add_css_class("orion-core-row")
        lock = Gtk.Image.new_from_icon_name("system-lock-screen-symbolic")
        lock.add_css_class("orion-locked")
        lock.set_margin_start(8)
        core.append(lock)
        cb = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1, margin_top=6, margin_bottom=6, margin_end=8)
        for text, css in ((CORE[0], "kind"), (CORE[1], "note")):
            lab = Gtk.Label(label=text, xalign=0, wrap=True)
            lab.add_css_class(css)
            cb.append(lab)
        core.append(cb)
        grid.attach(core, 0, y, 1, 1)
        dot = Gtk.Box(halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER, tooltip_text="Always ask (locked)")
        dot.add_css_class("orion-level-dot")
        dot.add_css_class("locked")
        grid.attach(dot, 1, y, 1, 1)
        for i in (2, 3):
            lk = Gtk.Image.new_from_icon_name("changes-prevent-symbolic")
            lk.add_css_class("orion-dim")
            lk.set_tooltip_text("Locked")
            grid.attach(lk, i, y, 1, 1)
        frame = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        frame.add_css_class("boxed-list")
        holder = Gtk.ListBoxRow(activatable=False)
        holder.set_child(grid)
        frame.append(holder)
        g.add(frame)
        lg = self.group("What Orion has learned",
                        "After a long run of yeses Orion may suggest raising a row. It never raises one itself: "
                        "that always waits for your phone.")
        for row in lv["rows"]:
            if row.get("streak"):
                lg.add(self.row(row["label"], f"{row['streak']} approvals in a row", icon="emblem-default-symbolic"))
        if not any(r.get("streak") for r in lv["rows"]):
            lg.add(self.row("Nothing yet", "Each yes or no on a self-change card counts here."))


class SelfUpdatesPage(UpdatesPage):
    def load(self) -> Any:
        return {"base": super().load(), "self": D.updates(self.sys.r, self.sys.root)}

    def _channel(self, ch: str) -> tuple[bool, str]:
        ok = self.sys.terminal(D.channel_command(ch) + "; echo; printf 'Press Enter. '; read _")
        return (True, "Type the orion-admin password in the terminal to switch.") if ok else \
            (False, "Couldn't open a terminal window.")

    def fill(self, d: dict[str, Any]) -> None:
        s = d["self"]
        g = self.group("How new versions arrive",
                       "Every new system version is tested before it's offered, and it always asks you first.")
        self.combo(g, "Update channel", "Stable is for everyday use. Changing it asks for the admin password.",
                   D.CHANNELS, s["channel"], self._channel)
        self.switch(g, "Test new versions in a VM first", "When this computer can run a VM. Otherwise only its own "
                    "checks run, and Orion says so.", s["vm_test"], lambda on: D.set_vm_test(self.sys.r, on),
                    sensitive=s["live"])
        words = D.offer_words(s["offer"])
        g.add(self.row("Look for a new version now", words or "Orion looks once a day by itself.",
                       icon="system-software-update-symbolic",
                       suffix=[self.button("Check", lambda: (self.win.toast("Checking and testing; this can take a "
                                                                            "while…"),
                                                             self.win.act(lambda: D.check_now(self.sys.r),
                                                                          after=self.refresh)),
                                           sensitive=s["live"])]))
        if s["firmware"]:
            g.add(self.row(f"{len(s['firmware'])} firmware update(s) waiting", "Orion never flashes firmware by "
                           "itself", icon="drive-harddisk-symbolic",
                           suffix=[self.terminal_button("Open", "fwupdmgr update")]))
        super().fill(d["base"])
