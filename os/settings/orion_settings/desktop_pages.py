"""Settings pages: "Keyboard & windows" (H13) and "This PC's hardware" (cheap feature 2), plus the window that sets
your password without a terminal (H5). Data side: desktop_settings.py and admin.py (tested without GTK)."""
from __future__ import annotations

from typing import Any, Callable, Optional

from gi.repository import Adw, Gtk  # noqa: E402  (pages.py has already required Gtk 4 / Adw 1)

from . import admin as A
from . import desktop_settings as D
from .pages import Page


class KeyboardWindowsPage(Page):
    key, title, icon, section = "keyboard", "Keyboard & windows", "input-keyboard-symbolic", "Devices"
    blurb = "Your keyboard layout, title bars with window buttons, and the shortcuts for switching windows."

    def load(self) -> Any:
        return {"kb": D.keyboard(self.sys), "win": D.windows(self.sys), "grav": D.gravity(self.sys)}

    def fill(self, d: dict[str, Any]) -> None:
        kb, win = d["kb"], d["win"]
        g = self.group("Keyboard")
        if not kb.get("available"):
            self.missing(g, "Keyboard layout", "orion-keyboard")
        else:
            r = self.combo(g, "Layout", "What the keys type. Search by language or country.", kb["choices"],
                           kb["current"], lambda v: D.set_keyboard(self.sys, v))
            try:   # libadwaita 1.4+: type to search the long list
                r.set_expression(Gtk.PropertyExpression.new(Gtk.StringObject, None, "string"))
                r.set_enable_search(True)
            except (AttributeError, TypeError):
                pass
            r.add_prefix(Gtk.Image.new_from_icon_name("input-keyboard-symbolic"))
        wg = self.group("Windows")
        if not win.get("available"):
            self.missing(wg, "Window buttons", "orion-windows")
            return
        self.switch(wg, "Window buttons", "A title bar on every app window. Right-click it (or Super+W) for Close, "
                    "Maximise, Minimise and Float. Alt+F4 closes.", win["buttons"],
                    lambda on: D.set_buttons(self.sys, on))
        wg.add(self.row("Switch windows", "Alt+Tab: the window you used before is first; Tab moves down, Enter "
                        "picks. Your open windows are also on the Orion taskbar.", icon="view-dual-symbolic"))
        if win.get("shortcuts"):
            wg.add(self.row("All keyboard shortcuts", "Hold Super, or press Super+/", icon="preferences-desktop-keyboard-shortcuts-symbolic",
                            suffix=[self.launch_button("Show", ["orion-shortcuts", "toggle"])]))
        # [look] Gravity mode (after-1.0 plan §3): off by default; needs the optional Hyprland session
        grav = d.get("grav") or {}
        if grav.get("available"):
            gg = self.group("Gravity mode")
            self.switch(gg, "Gravity mode", "Windows near the black hole bend toward it. Uses more graphics power, so it "
                        "pauses on battery and with reduced motion. " + grav.get("reason", ""), grav["on"],
                        lambda on: D.set_gravity(self.sys, on))
            if grav.get("installed"):
                self.switch(gg, "Use the Hyprland desktop", "Gravity mode needs it. Starts at your next login; turn it "
                            "off to go back to the normal desktop.", grav["hyprland"],
                            lambda on: D.set_hyprland(self.sys, on))
            else:
                gg.add(self.row("Hyprland desktop not installed", "Gravity mode can only bend windows in the Hyprland "
                                "desktop. It's optional and can be added when OrionOS is set up.",
                                icon="dialog-information-symbolic"))


class HardwarePage(Page):
    key, title, icon, section = "hardware", "This PC's hardware", "computer-symbolic", "System"
    blurb = ("Will this PC be happy with OrionOS? Graphics, Wi-Fi, Bluetooth, sound, camera, sleep, battery, screen "
             "and touchpad, checked in plain words. Run it from the USB stick before installing.")

    def load(self) -> Any:
        return D.hardware(self.sys)

    def fill(self, rep: Any) -> None:
        g = self.group()
        if rep is None:
            self.missing(g, "Hardware check", "orion-check-hardware")
            return
        title, sub, kind = D.headline(rep)
        top = self.row(title, sub, icon="emblem-ok-symbolic" if kind == "ok" else "dialog-warning-symbolic",
                       suffix=[self.button("Check again", lambda: self.refresh(), style="flat",
                                           icon="view-refresh-symbolic"),
                               self.button("Send to my phone", lambda: self.win.act(lambda: D.send_hardware(self.sys)))])
        top.add_css_class("orion-current" if kind == "ok" else "orion-warn")
        g.add(top)
        fails = [r for r in rep["rows"] if r["status"] == "fail"]
        rest = [r for r in rep["rows"] if r["status"] != "fail"]
        if fails:
            fg = self.group("Fix these first")
            for r in fails:
                fg.add(self._row(r))
        cg = self.group("Checked")
        for r in rest:
            cg.add(self._row(r))

    def _row(self, r: dict[str, Any]) -> Adw.ActionRow:
        text, kind = D.PILL.get(r["status"], ("", ""))
        sub = r["text"] + (f"\nFix: {r['fix']}" if r.get("fix") and r["status"] in ("fail", "warn") else "")
        row = self.row(r["label"], sub, icon=D.ICONS.get(r["id"], "emblem-system-symbolic"),
                       suffix=[self.pill(text, kind)] if text else [])
        row.set_subtitle_lines(0)
        return row


def ask_password(page: Page, then: Optional[Callable[[], tuple[bool, str]]] = None) -> None:
    """Choose a password for your account in a window (no terminal); the admin password is asked for next, in the
    system's password window. `then` runs right after a password was set (e.g. turning on the login screen)."""
    box = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
    box.add_css_class("boxed-list")
    one = Adw.PasswordEntryRow(title="New password (8+ characters)")
    two = Adw.PasswordEntryRow(title="The same again")
    box.append(one)
    box.append(two)

    def go() -> None:
        pw, again = one.get_text(), two.get_text()
        one.set_text("")
        two.set_text("")
        problem = A.password_problem(pw, again)
        if problem:
            page.win.toast(problem, long=True)
            return
        page.win.toast("The admin password window opens next.")

        def job() -> tuple[bool, str]:
            ok, msg = A.set_password(page.sys, pw)
            if ok and then is not None:
                ok2, msg2 = then()
                return ok2, f"{msg} {msg2}"
            return ok, msg
        page.win.act(job, after=page.refresh)
    page.win.confirm("Your password", "It unlocks the screen (Super+L) and, if you turn it on, the login screen. "
                     "Changing it needs the admin password.", "Set password", go, extra=box)
