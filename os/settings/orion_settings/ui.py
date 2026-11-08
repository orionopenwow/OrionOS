"""Orion Settings window: a sidebar of pages (Adw.NavigationSplitView) in the Orion Glass style.

Pages read the system in a background thread (backend.System) and rebuild their rows when the answer comes back,
so a slow tool never freezes the window. Every change shows a short plain-words message (a toast); failures say
what went wrong in plain words."""
from __future__ import annotations

import threading
from pathlib import Path
from typing import Any, Callable, Optional

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, GLib, Gtk  # noqa: E402

from . import pages  # noqa: E402
from .backend import System  # noqa: E402

APP_ID = "ai.orion.Settings"
CSS = Path(__file__).resolve().parent.parent / "style.css"


def in_background(fn: Callable[[], Any], done: Callable[[Any], None]) -> None:
    """Run fn off the UI thread and hand its result to done on the UI thread."""
    def work() -> None:
        try:
            res = fn()
        except Exception as exc:  # a bug in a page must not take the window down
            res = exc
        GLib.idle_add(lambda: (done(res), False)[1])
    threading.Thread(target=work, daemon=True).start()


class SettingsWindow(Adw.ApplicationWindow):
    def __init__(self, app: Adw.Application, system: System, first: Optional[str] = None,
                 solid: Optional[bool] = None) -> None:
        super().__init__(application=app, title="Settings")
        self.system = system
        self.set_default_size(1040, 700)
        self.set_size_request(360, 420)
        self.add_css_class("orion-settings")
        if solid is None:
            solid = self._theme_is_solid()
        if solid:
            self.add_css_class("solid")

        self.toasts = Adw.ToastOverlay()
        self.split = Adw.NavigationSplitView()
        self.split.set_min_sidebar_width(232)
        self.split.set_max_sidebar_width(272)
        self.split.set_sidebar_width_fraction(0.25)
        self.toasts.set_child(self.split)
        self.set_content(self.toasts)

        # narrow windows: the sidebar and the page become two screens
        bp = Adw.Breakpoint.new(Adw.BreakpointCondition.parse("max-width: 640sp"))
        bp.add_setter(self.split, "collapsed", True)
        self.add_breakpoint(bp)

        self.pages = [cls(self) for cls in pages.ALL]
        self._content: dict[str, Adw.NavigationPage] = {}
        self.split.set_sidebar(self._sidebar())
        self.show_page(first if first in {p.key for p in self.pages} else self.pages[0].key)

    @staticmethod
    def _theme_is_solid() -> bool:
        s = Gtk.Settings.get_default()
        name = s.get_property("gtk-theme-name") if s else ""
        try:
            from gi.repository import Gio
            src = Gio.SettingsSchemaSource.get_default()
            if src and src.lookup("org.gnome.desktop.interface", True):
                name = Gio.Settings.new("org.gnome.desktop.interface").get_string("gtk-theme") or name
        except Exception:
            pass
        return "Solid" in (name or "")

    # ------------------------------------------------------------------ sidebar
    def _sidebar(self) -> Adw.NavigationPage:
        header = Adw.HeaderBar()
        header.set_show_end_title_buttons(False)
        brand = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        word = Gtk.Label(label="SETTINGS")
        word.add_css_class("orion-wordmark")
        brand.append(word)
        header.set_title_widget(brand)

        self.nav = Gtk.ListBox()
        self.nav.add_css_class("orion-nav")
        self.nav.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self.nav.set_header_func(self._nav_header)
        for p in self.pages:
            row = Gtk.ListBoxRow()
            row.page_key = p.key  # type: ignore[attr-defined]
            row.section = p.section  # type: ignore[attr-defined]
            box = Gtk.Box(spacing=12)
            box.append(Gtk.Image.new_from_icon_name(p.icon))
            lab = Gtk.Label(label=p.title, xalign=0)
            lab.set_ellipsize(3)
            box.append(lab)
            row.set_child(box)
            self.nav.append(row)
        self.nav.connect("row-activated", lambda _l, row: self.show_page(row.page_key))

        scroll = Gtk.ScrolledWindow(vexpand=True)
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_child(self.nav)

        foot = Gtk.Box(spacing=6, margin_start=12, margin_end=12, margin_bottom=12, margin_top=6)
        for label, icon, act in (("Log out", "system-log-out-symbolic", "logout"),
                                 ("Restart", "system-reboot-symbolic", "reboot"),
                                 ("Power off", "system-shutdown-symbolic", "poweroff")):
            b = Gtk.Button(icon_name=icon, tooltip_text=label)
            b.add_css_class("flat")
            b.update_property([Gtk.AccessibleProperty.LABEL], [label])
            b.connect("clicked", lambda _b, a=act, l=label: self.confirm_session(a, l))
            foot.append(b)

        tv = Adw.ToolbarView()
        tv.add_top_bar(header)
        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        body.append(scroll)
        body.append(foot)
        tv.set_content(body)
        tv.add_css_class("orion-sidebar-pane")
        page = Adw.NavigationPage(title="Settings", child=tv)
        return page

    @staticmethod
    def _nav_header(row: Gtk.ListBoxRow, before: Optional[Gtk.ListBoxRow]) -> None:
        if before is None or before.section != row.section:  # type: ignore[attr-defined]
            lab = Gtk.Label(label=row.section.upper(), xalign=0)  # type: ignore[attr-defined]
            lab.add_css_class("orion-nav-section")
            row.set_header(lab)
        else:
            row.set_header(None)

    def show_page(self, key: str) -> None:
        page = next(p for p in self.pages if p.key == key)
        if key not in self._content:
            header = Adw.HeaderBar()
            header.set_show_start_title_buttons(False)
            header.set_title_widget(Gtk.Label())   # the page shows its own big title
            refresh = Gtk.Button(icon_name="view-refresh-symbolic", tooltip_text="Check again")
            refresh.add_css_class("flat")
            refresh.connect("clicked", lambda _b: page.refresh())
            header.pack_end(refresh)
            tv = Adw.ToolbarView()
            tv.add_top_bar(header)
            tv.set_content(page.widget)
            self._content[key] = Adw.NavigationPage(title=page.title, child=tv, tag=key)
            page.refresh()
        self.split.set_content(self._content[key])
        self.split.set_show_content(True)
        for row in list(self.nav):
            if getattr(row, "page_key", None) == key:
                self.nav.select_row(row)

    # ------------------------------------------------------------------ feedback
    def toast(self, text: str, long: bool = False) -> None:
        if not text:
            return
        t = Adw.Toast(title=text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))
        t.set_timeout(6 if long else 3)
        self.toasts.add_toast(t)

    def act(self, fn: Callable[[], tuple[bool, str]], after: Optional[Callable[[], None]] = None,
            on_fail: Optional[Callable[[], None]] = None) -> None:
        """Run a change in the background, say how it went, then refresh."""
        def done(res: Any) -> None:
            if isinstance(res, Exception):
                ok, msg = False, "Something went wrong there. Nothing else was changed."
            else:
                ok, msg = res
            self.toast(msg, long=not ok)
            if not ok and on_fail:
                on_fail()
            if after:
                after()
        in_background(fn, done)

    def confirm(self, heading: str, body: str, yes: str, on_yes: Callable[[], None], destructive: bool = False,
                extra: Optional[Gtk.Widget] = None) -> None:
        d = Adw.AlertDialog(heading=heading, body=body)
        d.add_response("cancel", "Cancel")
        d.add_response("yes", yes)
        d.set_response_appearance("yes", Adw.ResponseAppearance.DESTRUCTIVE if destructive
                                  else Adw.ResponseAppearance.SUGGESTED)
        d.set_default_response("cancel" if destructive else "yes")
        d.set_close_response("cancel")
        if extra is not None:
            d.set_extra_child(extra)
        d.connect("response", lambda _d, r: on_yes() if r == "yes" else None)
        d.present(self)

    def confirm_session(self, action: str, label: str) -> None:
        body = {"logout": "Your open apps close. Orion keeps running in the background.",
                "reboot": "The computer restarts. Save your work first.",
                "poweroff": "The computer turns off. Save your work first."}[action]
        argv = {"logout": ["swaymsg", "exit"], "reboot": ["systemctl", "reboot"],
                "poweroff": ["systemctl", "poweroff"]}[action]
        self.confirm(f"{label}?", body, label, lambda: self.system.launch(argv), destructive=True)


class SettingsApp(Adw.Application):
    def __init__(self, system: Optional[System] = None, first: Optional[str] = None,
                 solid: Optional[bool] = None) -> None:
        super().__init__(application_id=APP_ID)
        self.system = system or System()
        self.first = first
        self.solid = solid
        self.win: Optional[SettingsWindow] = None

    def do_startup(self) -> None:  # noqa: N802 (GTK naming)
        Adw.Application.do_startup(self)
        sm = Adw.StyleManager.get_default()
        sm.set_color_scheme(Adw.ColorScheme.FORCE_DARK)
        settings = Gtk.Settings.get_default()
        if settings is not None:
            settings.set_property("gtk-decoration-layout", "close,minimize,maximize:")
        prov = Gtk.CssProvider()
        try:
            prov.load_from_path(str(CSS))
        except GLib.Error:
            pass
        Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), prov,
                                                  Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        for accel, name in (("<Ctrl>q", "quit"), ("<Ctrl>w", "quit")):
            self.set_accels_for_action(f"app.{name}", [accel])
        from gi.repository import Gio
        quit_action = Gio.SimpleAction.new("quit", None)
        quit_action.connect("activate", lambda *_: self.quit())
        self.add_action(quit_action)

    def do_activate(self) -> None:  # noqa: N802
        if self.win is None:
            self.win = SettingsWindow(self, self.system, self.first, self.solid)
        self.win.present()
