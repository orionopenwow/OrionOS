"""The Rescue panel (GTK 4, no libadwaita needed, software drawing so it works on any screen or VM).

Opens by itself when the Orion screen keeps crashing (os/lab/bin/orion-hud), in the Rescue desktop (the login
screen's "Rescue desktop" button), and with Ctrl+Alt+Delete or Super+Escape at any time:

    Terminal  Files  Browser
    Undo the last update          (orion-slot / orion-snapshot / Orion's change history: see undo.py)
    Restart into Windows          (only when orion-restart-windows is installed)
    Send a report to my phone
    Try Orion again               (closes the panel; the Orion screen starts again)
"""
from __future__ import annotations

import shutil
import subprocess
import threading
from typing import Any, Callable

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gtk  # noqa: E402

from . import crashes, report, undo  # noqa: E402

APP_ID = "ai.orion.Rescue"
CSS = b"""
window.orion-rescue { background: #05070E; color: #D8DEFF; }
.orion-rescue .title { font-size: 26px; font-weight: 600; }
.orion-rescue .sub { color: #A5B4FC; font-size: 14px; }
.orion-rescue button.big { padding: 14px 16px; border-radius: 14px; background: #0B0F1E; border: 1px solid #262D50;
  color: #D8DEFF; }
.orion-rescue button.big:hover, .orion-rescue button.big:focus { border-color: #7C5CFF; background: #12172C; }
.orion-rescue button.main { border-color: #7C5CFF; }
.orion-rescue .btitle { font-size: 15px; font-weight: 600; }
.orion-rescue .bsub { color: #8890B8; font-size: 12px; }
.orion-rescue .status { color: #00E5FF; font-size: 13px; }
"""


def first(*cmds: str) -> str:
    for c in cmds:
        p = shutil.which(c)
        if p:
            return p
    return ""


def launch(argv: list[str]) -> bool:
    if not argv or not argv[0]:
        return False
    try:
        subprocess.Popen(argv, start_new_session=True, stdin=subprocess.DEVNULL,   # noqa: S603 (fixed argv)
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    except OSError:
        return False


def background(fn: Callable[[], Any], done: Callable[[Any], None]) -> None:
    def work() -> None:
        try:
            res = fn()
        except Exception as exc:   # never let a bug close the rescue panel
            res = (False, f"That didn't work ({type(exc).__name__}).")
        GLib.idle_add(lambda: (done(res), False)[1])
    threading.Thread(target=work, daemon=True).start()


class RescueWindow(Gtk.ApplicationWindow):
    def __init__(self, app: Gtk.Application, mode: str) -> None:
        super().__init__(application=app, title="Rescue")
        self.mode = mode
        self.add_css_class("orion-rescue")
        self.set_default_size(760, 600)
        self.plan: undo.UndoPlan | None = None

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10, margin_top=28, margin_bottom=22,
                        margin_start=28, margin_end=28)
        title = {"crash": "The Orion screen keeps stopping",
                 "session": "Rescue desktop"}.get(mode, "Rescue")
        sub = {"crash": "Your files and apps are fine. Pick what you need below; nothing here deletes anything.",
               "session": "A plain desktop without the Orion screen. Your files and apps are all here."
               }.get(mode, "Tools for when something isn't working right.")
        t = Gtk.Label(label=title, xalign=0, wrap=True)
        t.add_css_class("title")
        s = Gtk.Label(label=sub, xalign=0, wrap=True)
        s.add_css_class("sub")
        outer.append(t)
        outer.append(s)

        grid = Gtk.Grid(column_spacing=10, row_spacing=10, margin_top=14, column_homogeneous=True)
        term = first("foot", "x-terminal-emulator")
        files = first("orion-files", "thunar", "nautilus")
        browser = first("orion-browser", "firefox", "chromium", "epiphany")
        grid.attach(self.big("utilities-terminal-symbolic", "Terminal", "Type commands", lambda: launch([term])),
                    0, 0, 1, 1)
        grid.attach(self.big("system-file-manager-symbolic", "Files", "Your folders", lambda: launch([files])),
                    1, 0, 1, 1)
        grid.attach(self.big("web-browser-symbolic", "Browser", "The web", lambda: launch([browser])), 2, 0, 1, 1)

        self.undo_btn, self.undo_sub = self.big_with_sub("edit-undo-symbolic", "Undo the last update",
                                                         "Checking what can be undone…", self.on_undo)
        self.undo_btn.set_sensitive(False)
        grid.attach(self.undo_btn, 0, 1, 3, 1)
        row = 2
        win = shutil.which("orion-restart-windows")
        if win:
            grid.attach(self.big("system-reboot-symbolic", "Restart into Windows",
                                 "Starts Windows once; OrionOS stays as it is", lambda: self.restart_windows(win)),
                        0, row, 3, 1)
            row += 1
        grid.attach(self.big("phone-symbolic", "Send a report to my phone",
                             "What went wrong, without any passwords or keys", self.on_report), 0, row, 3, 1)
        row += 1
        again = ("Log out and start Orion from the login screen" if mode == "session"
                 else "Closes this panel and starts the Orion screen again")
        tb = self.big("view-refresh-symbolic", "Try Orion again", again, self.try_again)
        tb.add_css_class("main")
        grid.attach(tb, 0, row, 3, 1)
        outer.append(grid)

        self.status = Gtk.Label(label="", xalign=0, wrap=True, margin_top=6)
        self.status.add_css_class("status")
        outer.append(self.status)
        hint = Gtk.Label(label="Ctrl+Alt+Delete or Super+Escape opens this panel any time.", xalign=0)
        hint.add_css_class("bsub")
        outer.append(hint)
        scroller = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER)
        scroller.set_child(outer)
        self.set_child(scroller)
        background(undo.plan_last_update, self.got_plan)

    # ---- building blocks -------------------------------------------------------------------------------
    def big_with_sub(self, icon: str, title: str, sub: str, cb: Callable[[], None]) -> tuple[Gtk.Button, Gtk.Label]:
        b = Gtk.Button()
        b.add_css_class("big")
        box = Gtk.Box(spacing=12)
        box.append(Gtk.Image.new_from_icon_name(icon))
        col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        tl = Gtk.Label(label=title, xalign=0)
        tl.add_css_class("btitle")
        sl = Gtk.Label(label=sub, xalign=0, wrap=True)
        sl.add_css_class("bsub")
        col.append(tl)
        col.append(sl)
        box.append(col)
        b.set_child(box)
        b.update_property([Gtk.AccessibleProperty.LABEL], [title])
        b.connect("clicked", lambda _b: cb())
        return b, sl

    def big(self, icon: str, title: str, sub: str, cb: Callable[[], Any]) -> Gtk.Button:
        def click() -> None:
            res = cb()
            if res is False:
                self.say(f"Couldn't open {title.lower()}.")
        return self.big_with_sub(icon, title, sub, click)[0]

    def say(self, text: str) -> None:
        self.status.set_label(text)

    # ---- actions ---------------------------------------------------------------------------------------
    def got_plan(self, plan: Any) -> None:
        if not isinstance(plan, undo.UndoPlan):
            self.undo_sub.set_label("Couldn't check what can be undone.")
            return
        self.plan = plan
        self.undo_sub.set_label(f"{plan.title}. {plan.detail}" if plan.possible else plan.detail)
        self.undo_btn.set_sensitive(plan.possible)

    def on_undo(self) -> None:
        if not self.plan or not self.plan.possible:
            return
        self.undo_btn.set_sensitive(False)
        self.say("Working on it…")

        def done(res: Any) -> None:
            ok, msg = res
            self.say(msg)
            self.undo_btn.set_sensitive(True)
        background(lambda: undo.run(self.plan), done)   # type: ignore[arg-type]

    def on_report(self) -> None:
        self.say("Putting the report together…")
        background(report.send, lambda res: self.say(res[1]))

    def restart_windows(self, exe: str) -> None:
        self.say("Restarting into Windows…" if launch([exe]) else "Couldn't start the restart into Windows.")

    def try_again(self) -> None:
        crashes.reset()
        if self.mode == "session":
            launch(["swaymsg", "exit"])
        self.get_application().quit()


class RescueApp(Gtk.Application):
    def __init__(self, mode: str) -> None:
        super().__init__(application_id=APP_ID)
        self.mode = mode

    def do_activate(self) -> None:   # noqa: N802 (GTK naming)
        win = self.get_active_window()
        if win is None:
            prov = Gtk.CssProvider()
            if hasattr(prov, "load_from_string"):
                prov.load_from_string(CSS.decode())
            else:
                prov.load_from_data(CSS, -1)
            from gi.repository import Gdk
            Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), prov,
                                                      Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
            win = RescueWindow(self, self.mode)
        win.present()


def main(mode: str = "") -> int:
    app = RescueApp(mode)
    return app.run([])
