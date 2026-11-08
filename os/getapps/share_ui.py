#!/usr/bin/env python3
"""Share files, first run: asks once to install LocalSend from Flathub (the owner's click), shows the progress,
then hands over to `orion-share launch` (LocalSend with the sharing port open while it runs)."""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any, Optional

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, Gtk  # noqa: E402

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import getapps_ui as ui  # noqa: E402
import orion_apps as core  # noqa: E402
import orion_share as share  # noqa: E402

APP_ID = "local.ShareFiles"


class ShareWindow(Adw.ApplicationWindow):
    def __init__(self, app: "ShareApp") -> None:
        super().__init__(application=app, title="Share files")
        self.app = app
        self.add_css_class("ga-window")
        self.set_default_size(760, 540)
        self.fp = core.Flatpak()
        view = Adw.ToolbarView()
        view.add_top_bar(Adw.HeaderBar(title_widget=Gtk.Label(label="")))
        self.set_content(view)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10, margin_start=36, margin_end=36,
                      margin_top=8, margin_bottom=28)
        view.set_content(box)

        head = Gtk.Box(spacing=16)
        head.append(ui.app_icon(share.LOCALSEND, "folder-publicshare", 64))
        names = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, valign=Gtk.Align.CENTER, spacing=4)
        names.append(ui.label("Share files", "ga-h1"))
        names.append(ui.label("Between this computer, Windows PCs, phones and Macs on the same Wi-Fi", "ga-lead"))
        head.append(names)
        box.append(head)

        hero = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        hero.add_css_class("ga-hero")
        hero.append(ui.label("Uses LocalSend", "ga-name"))
        hero.append(ui.label("A free, open-source app for Windows, Android, iPhone, Mac and Linux. Files go straight "
                             "from one device to the other over your own network: no account, no cloud, nothing "
                             "uploaded.", "ga-lead", wrap=True))
        box.append(hero)

        safe = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        safe.add_css_class("ga-safe")
        safe.append(ui.label("How Orion keeps it safe", "ga-name"))
        for line in ("LocalSend comes from Flathub and runs in its own sandbox.",
                     f"The firewall opens LocalSend's port ({share.PORT}) only while it's running, and only to "
                     "your home network. It closes again when you quit LocalSend.",
                     "Every file someone sends you waits for your OK in LocalSend first."):
            row = Gtk.Box(spacing=8)
            row.append(ui.label("●", "ga-history-ok"))
            text = ui.label(line, "ga-dim", wrap=True)
            text.set_hexpand(True)
            row.append(text)
            safe.append(row)
        box.append(safe)

        box.append(Gtk.Box(vexpand=True))
        self.status = ui.label("LocalSend isn't on this computer yet. Install it once from Flathub; the first time, Flatpak may also "
                               "download shared parts it needs.",
                               "ga-dim", wrap=True)
        box.append(self.status)
        self.bar = Gtk.ProgressBar(visible=False)
        self.bar.add_css_class("ga-progress")
        box.append(self.bar)
        buttons = Gtk.Box(spacing=10)
        other = Gtk.LinkButton(uri="https://localsend.org/download", label="Get LocalSend for your phone or PC",
                               valign=Gtk.Align.CENTER)
        other.add_css_class("flat")
        buttons.append(other)
        buttons.append(Gtk.Box(hexpand=True))
        self.later = ui.button("Not now", "ga-quiet", lambda _b: self.close())
        self.go = ui.button("Install LocalSend", "suggested-action ga-install", lambda _b: self.install())
        buttons.append(self.later)
        buttons.append(self.go)
        box.append(buttons)

    def install(self) -> None:
        self.go.set_sensitive(False)
        self.later.set_sensitive(False)
        self.bar.set_visible(True)
        self.status.set_text("Installing LocalSend from Flathub…")
        GLib.timeout_add(150, self._pulse)

        def progress(pct: Optional[int], line: str) -> None:
            GLib.idle_add(self._progress, pct, line)

        def done(res: dict[str, Any]) -> None:
            self.bar.set_visible(False)
            if res.get("ok"):
                self.status.set_text("Installed. Opening LocalSend…")
                self.app.launch_after = True
                GLib.timeout_add(600, lambda: (self.close(), False)[1])
            else:
                self.status.set_text(res.get("message", "LocalSend couldn't be installed."))
                self.go.set_sensitive(True)
                self.later.set_sensitive(True)
        ui.run_bg(lambda: core.act_install(self.fp, share.LOCALSEND, progress=progress), done)

    def _pulse(self) -> bool:
        if not self.bar.get_visible():
            return False
        if self.bar.get_fraction() == 0:
            self.bar.pulse()
        return True

    def _progress(self, pct: Optional[int], _line: str) -> bool:
        if pct is not None:
            self.bar.set_fraction(pct / 100.0)
            self.status.set_text(f"Installing LocalSend from Flathub… {pct}%")
        return False


class ShareApp(Adw.Application):
    def __init__(self) -> None:
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.NON_UNIQUE
                         if os.environ.get("ORION_GETAPPS_NON_UNIQUE") else Gio.ApplicationFlags.DEFAULT_FLAGS)
        self.launch_after = False

    def do_startup(self) -> None:
        ui.GetApps.do_startup(self)   # same look: Orion colours, Orion Glass, getapps.css

    def do_activate(self) -> None:
        (self.props.active_window or ShareWindow(self)).present()


def main() -> int:
    GLib.set_prgname(APP_ID)
    GLib.set_application_name("Share files")
    app = ShareApp()
    code = app.run([sys.argv[0]])
    if app.launch_after:
        os.execv(sys.executable, [sys.executable, str(HERE / "orion_share.py"), "launch"])
    return code


if __name__ == "__main__":
    sys.exit(main())
