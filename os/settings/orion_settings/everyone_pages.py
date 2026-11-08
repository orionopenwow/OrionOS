"""Settings pages for "Everyone can use it" (complete plan step 6): Accessibility and Languages & typing, plus the
extra groups on the Power page (battery care) and the Users page (guest account, add a person).
The system side is everyone.py."""
from __future__ import annotations

import re
from typing import Any

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk  # noqa: E402

from . import everyone as EV  # noqa: E402
from .pages import NOT_INSTALLED, Page  # noqa: E402

KEYS = [("Super+Alt+S", "Screen reader on or off"), ("Super+Alt+K", "Show or hide the on-screen keyboard"),
        ("Super+Alt+C", "High contrast on or off"), ("Super+Alt+= / Super+Alt+-", "Zoom the screen in or out"),
        ("Super+Alt+0", "Zoom back to normal"), ("Ctrl+= / Ctrl+-", "Zoom the Orion window"),
        ("Super+.", "Pick an emoji")]


def _val(x: Any) -> str:
    return "off" if x in (None, False) else f"{float(x):g}"


class AccessibilityPage(Page):
    key, title, icon, section = "accessibility", "Accessibility", "preferences-desktop-accessibility-symbolic", "System"
    blurb = "Help with seeing, hearing and typing. Every choice works straight away and stays after you log out."

    def load(self) -> Any:
        return EV.access(self.sys)

    def fill(self, d: dict[str, Any]) -> None:
        if not d["available"]:
            g = self.group()
            self.missing(g, "Accessibility", "orion-access")
            return
        set_ = lambda what: (lambda v: EV.set_access(self.sys, what, v))  # noqa: E731
        onoff = lambda what: (lambda on: EV.set_access(self.sys, what, "on" if on else "off"))  # noqa: E731
        g = self.group("Seeing")
        self.switch(g, "Screen reader", "Orca reads out what's on screen and what you type. Super+Alt+S. Works best "
                    "in Firefox, the Files app and Settings; it hasn't been fully tested with Sway yet."
                    if d["orca"] else NOT_INSTALLED, d.get("screen_reader", False), onoff("screen-reader"),
                    sensitive=d["orca"])
        self.switch(g, "High contrast", "White and yellow on black, thick edges, in apps, the terminal, window "
                    "borders and the Orion window. Super+Alt+C.", d.get("contrast", False), onoff("contrast"))
        self.combo(g, "Text size", "Apps, the terminal, the Orion window and the mouse pointer.", EV.TEXT_SIZES,
                   _val(d.get("text_scale", 1)), set_("text-size"))
        self.combo(g, "Make everything bigger", "Every screen, all at once. A size you chose for one screen in "
                   "Displays still wins there.", EV.SCREEN_SIZES, _val(d.get("screen_scale")), set_("screen-size"))
        self.combo(g, "Orion window zoom", "Just the Orion window. Ctrl+= and Ctrl+- there do the same.",
                   EV.HUD_ZOOMS, _val(d.get("hud_zoom", 1)), set_("hud-zoom"))
        g.add(self.row("Zoom the screen", "Super+Alt+= zooms in, Super+Alt+- zooms out, Super+Alt+0 goes back. It "
                       "makes the screen you're on bigger rather than following the mouse like a magnifier (that "
                       "isn't possible on this desktop yet).", icon="zoom-in-symbolic"))
        tg = self.group("Typing")
        self.switch(tg, "On-screen keyboard", "A keyboard at the bottom of the screen, for touch screens or when "
                    "typing is hard. Super+Alt+K shows or hides it." if d["osk"] else NOT_INSTALLED,
                    d.get("keyboard", False), onoff("keyboard"), sensitive=d["osk"])
        self.switch(tg, "Ready on touch screens", "On a touch screen the keyboard waits out of sight; Super+Alt+K "
                    "brings it up. (It can't pop up by itself when you tap a text box yet.)",
                    d.get("keyboard_touch", True), onoff("keyboard-touch"), sensitive=d["osk"])
        tg.add(self.row("Other languages and emoji", "Settings > Languages & typing", icon="input-keyboard-symbolic",
                        suffix=[self.button("Open", lambda: self.win.show_page("languages"))]))
        mg = self.group("Motion")
        self.switch(mg, "Reduce motion", "Simple fades instead of windows growing and spiralling. The same switch as "
                    "in Appearance; the Orion window follows it too.", not d["animations"],
                    lambda on: self.sys.set_animations(not on))
        hg = self.group("Hearing")
        hg.add(self.row("Everything Orion says is also written", "Spoken replies always show as text in the Orion "
                        "window, and alerts show as notifications.", icon="view-reveal-symbolic"))
        if d["boot_sound"] is not None:
            self.switch(hg, "Start-up sound", "", d["boot_sound"], self.sys.set_boot_sound)
        kg = self.group("Keys")
        for k, what in KEYS:
            kg.add(self.row(what, k, icon="input-keyboard-symbolic"))


class LanguagesPage(Page):
    key, title, icon, section = "languages", "Languages & typing", "preferences-desktop-locale-symbolic", "System"
    blurb = "Type in more than one language, and pick emoji."

    def load(self) -> Any:
        return EV.typing(self.sys)

    def fill(self, d: dict[str, Any]) -> None:
        g = self.group("Your languages", "With two or more, Ctrl+Space switches between them while you type.")
        if not d["available"]:
            self.missing(g, "Languages", "orion-typing")
            return
        langs = d.get("languages", [])
        for lang in langs:
            suffix = [] if len(langs) < 2 else [self.button("Remove", lambda i=lang["id"]: self.win.act(
                lambda: EV.typing_change(self.sys, "remove", i), after=self.refresh))]
            g.add(self.row(lang["name"], "Keyboard layout" if lang["id"].startswith("keyboard-") else "Input method",
                           icon="input-keyboard-symbolic", suffix=suffix))
        if d.get("missing"):
            g.add(self.row("Some languages need one more piece", "Not installed yet: " + ", ".join(d["missing"]) +
                           ". OrionOS updates add it, or ask Orion to install it.", icon="dialog-warning-symbolic"))
        if not d.get("fcitx5"):
            g.add(self.row("Input methods aren't installed", "Only the first language works until fcitx5 is "
                           "installed.", icon="dialog-warning-symbolic"))
        have = {x["id"] for x in langs}
        choices = [c for c in d.get("choices", []) if c["id"] not in have]
        if choices:
            ag = self.group("Add a language")
            drop = Gtk.DropDown.new_from_strings([c["name"] for c in choices])
            drop.set_valign(Gtk.Align.CENTER)
            drop.set_enable_search(True)

            def add() -> None:
                c = choices[drop.get_selected()]
                self.win.act(lambda: EV.typing_change(self.sys, "add", c["id"]), after=self.refresh)
            ag.add(self.row("Language", "The first time, log out and back in so every app sees it.",
                            icon="list-add-symbolic", suffix=[drop, self.button("Add", add, style="suggested-action")]))
        eg = self.group("Emoji")
        eg.add(self.row("Emoji picker", "Super+. anywhere: type a word like “heart” or “rocket”, press Enter, and "
                        "it's typed for you (and copied).", icon="face-smile-symbolic",
                        suffix=[self.launch_button("Open", ["orion-typing", "emoji"])]))


# ------------------------------------------------------------------ extra groups on existing pages
def power_extra(page: Page, d: dict[str, Any]) -> None:
    """Battery care on the Power page (PowerPage.fill calls this with the page's data)."""
    care = d.get("care")
    if not care:
        return
    g = page.group("Battery care")
    if care.get("present"):
        g.add(page.row("Low battery warnings", f"A note at {care.get('warn_at', 15)}% and {care.get('critical_at', 7)}%; at "
                       f"{care.get('sleep_at', 4)}% the computer goes to sleep by itself so nothing is lost.",
                       icon="battery-caution-symbolic"))
        page.switch(g, "Save power on battery", f"Below {care.get('saver_below', 20)}% the speed switches to power saver, "
                    "and back when you plug in.", care.get("auto_saver", True),
                    lambda on: EV.set_power_pref(page.sys, "auto-saver", on))
    page.switch(g, "Sleep, then hibernate", care.get("hibernate_reason", ""), care.get("hibernate", False),
                lambda on: EV.set_power_pref(page.sys, "hibernate", on), sensitive=bool(care.get("hibernate_possible")))
    g.add(page.row("Lid closed with another screen plugged in", "The computer keeps running and the laptop screen "
                   "goes off (Displays).", icon="video-display-symbolic"))


def users_extra(page: Page, d: dict[str, Any]) -> None:
    """Other people on the Users page: the guest account and adding a person (both ask for the admin password)."""
    p = d.get("people") or {}
    og = page.group("Other people", "Each person has their own files, settings and Orion. Changes here ask for the "
                    "orion-admin password in a terminal.")
    if not p.get("available"):
        og.add(page.row("Guest account and other people", NOT_INSTALLED, icon="system-users-symbolic"))
        return
    if p.get("is_guest"):
        og.add(page.row("You're using the guest account", "Everything here is wiped when you log out. Orion works "
                        "as a simple assistant: no agents, messages or approvals.", icon="avatar-default-symbolic"))
        return
    for name in p.get("people", []):
        if name != p.get("me"):
            og.add(page.row(name, "Logs in with their own password", icon="avatar-default-symbolic"))
    sw = Adw.SwitchRow(title="Guest account", subtitle="For a visitor: no password, nothing of yours, and everything "
                       "they do is wiped when they log out.", active=bool(p.get("guest_enabled")))
    sw.connect("notify::active", lambda r, _p: page.win.toast(
        "Opened in a terminal window." if page.sys.terminal(EV.guest_command(r.get_active()))
        else "Couldn't open a terminal."))
    og.add(sw)
    if p.get("guest_enabled"):
        og.add(page.row("Start a guest session", "Logs you out (save your work first); the guest session starts "
                        "straight after. Your own login comes back when the guest logs out.",
                        icon="system-log-out-symbolic",
                        suffix=[page.button("Start", lambda: page.win.confirm(
                            "Start a guest session?", "Your open apps close. Save your work first.", "Log out",
                            lambda: page.win.act(lambda: EV.start_guest(page.sys)), destructive=True))]))
    name = Adw.EntryRow(title="Their name")
    login = Adw.EntryRow(title="Login name (small letters, like “sam”)")

    def suggest(_r: Any) -> None:
        if not login.get_text():
            first = re.sub(r"[^a-z0-9-]", "", (name.get_text().lower().split() or [""])[0])
            login.set_text(first)
    name.connect("apply", suggest)
    name.set_show_apply_button(True)

    def add() -> None:
        cmd = EV.add_person_command(login.get_text(), name.get_text())
        if not cmd:
            page.win.toast("Pick a login name: 2 to 31 small letters, numbers or dashes.", long=True)
            return
        page.win.toast("Opened in a terminal window." if page.sys.terminal(cmd) else "Couldn't open a terminal.")
    og.add(name)
    og.add(login)
    og.add(page.row("Add a person", "Then choose their password in the terminal. They log in after you log out.",
                    icon="list-add-symbolic", suffix=[page.button("Add", add, style="suggested-action")]))
