#!/usr/bin/env python3
"""OrionOS "Get apps" window (GTK 4 + libadwaita, Orion Glass look). Started by `orion-apps open`.

Pages: Discover (Orion picks by category, and Flathub search), Installed, Updates (safe "update all", paused apps,
the change history) and Orion Studio (the optional Craft suite, alpha). Every install, removal and update is the
owner's click here; the work itself is orion_apps.py, so the window and `orion-apps` behave the same.

    python3 getapps_ui.py [--page discover|installed|updates|windows|studio] [--app APP_ID]
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable, Optional

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk, Pango  # noqa: E402

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import orion_apps as core  # noqa: E402

# Application id without "orion" in it: the Sway config sends every app_id matching (?i)orion to the HUD's
# workspace, full screen.
APP_ID = "local.GetApps"
THEME_CSS = [Path("/usr/share/themes/Orion-Glass/gtk-4.0/orion-glass.css"),
             HERE.parent / "theme/gtk/Orion-Glass/gtk-4.0/orion-glass.css"]
COLOUR_CSS = [Path("/etc/orion/gtk-4.0/gtk.css"), HERE.parent / "theme/gtk-4.0/gtk.css"]
APPSTREAM_ICONS = ["/var/lib/flatpak/appstream/flathub/x86_64/active/icons/128x128",
                   "/var/lib/flatpak/appstream/flathub/x86_64/active/icons/64x64",
                   os.path.expanduser("~/.local/share/flatpak/appstream/flathub/x86_64/active/icons/128x128")]
EXPORT_ICONS = ["/var/lib/flatpak/exports/share/icons/hicolor/128x128/apps",
                "/var/lib/flatpak/exports/share/icons/hicolor/scalable/apps",
                os.path.expanduser("~/.local/share/flatpak/exports/share/icons/hicolor/128x128/apps")]
PAGES = [("discover", "Discover", "starred-symbolic"), ("installed", "Installed", "view-grid-symbolic"),
         ("updates", "Updates", "software-update-available-symbolic"),
         ("windows", "Run Windows apps", "applications-games-symbolic"),   # from-windows: Bottles, Steam, recipes
         ("studio", "Orion Studio", "applications-graphics-symbolic")]


def first_existing(paths: list[Path]) -> Optional[Path]:
    return next((p for p in paths if p.is_file()), None)


def label(text: str, css: str = "", wrap: bool = False, xalign: float = 0.0, lines: int = 0,
          selectable: bool = False) -> Gtk.Label:
    lb = Gtk.Label(label=text, xalign=xalign, wrap=wrap, selectable=selectable)
    if wrap:
        lb.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
    if lines:
        lb.set_lines(lines)
        lb.set_ellipsize(Pango.EllipsizeMode.END)
    for c in css.split():
        lb.add_css_class(c)
    return lb


def badge(text: str, kind: str = "") -> Gtk.Label:
    lb = label(text, f"ga-badge {kind}".strip())
    lb.set_valign(Gtk.Align.CENTER)
    return lb


def button(text: str, css: str, cb: Callable[[Gtk.Button], None], tooltip: str = "") -> Gtk.Button:
    b = Gtk.Button(label=text)
    for c in css.split():
        b.add_css_class(c)
    b.set_valign(Gtk.Align.CENTER)
    if tooltip:
        b.set_tooltip_text(tooltip)
    b.connect("clicked", cb)
    return b


def app_icon(app_id: str, hint: str = "", size: int = 48) -> Gtk.Image:
    for d in EXPORT_ICONS + APPSTREAM_ICONS:
        for ext in (".png", ".svg"):
            p = Path(d) / f"{app_id}{ext}"
            if p.is_file():
                img = Gtk.Image.new_from_file(str(p))
                img.set_pixel_size(size)
                img.add_css_class("ga-icon")
                return img
    names = [n for n in (app_id, hint, (hint or "").lower()) if n]
    theme = Gtk.IconTheme.get_for_display(Gdk.Display.get_default())
    name = next((n for n in names if theme.has_icon(n)), "")
    if name:
        img = Gtk.Image.new_from_icon_name(name)
    else:
        # The Orion icon theme inherits Papirus-Dark; ask it directly in case the session's theme is another one.
        paint = None
        for n in names:
            alt = _papirus()
            if alt is not None and alt.has_icon(n):
                paint = alt.lookup_icon(n, None, size, 1, Gtk.TextDirection.NONE, 0)
                break
        img = Gtk.Image.new_from_paintable(paint) if paint else Gtk.Image.new_from_icon_name("package-x-generic")
    img.set_pixel_size(size)
    img.add_css_class("ga-icon")
    return img


_PAPIRUS: list[Optional[Gtk.IconTheme]] = []


def _papirus() -> Optional[Gtk.IconTheme]:
    if not _PAPIRUS:
        t = None
        if Path("/usr/share/icons/Papirus-Dark").is_dir():
            t = Gtk.IconTheme()
            t.set_theme_name("Papirus-Dark")
        _PAPIRUS.append(t)
    return _PAPIRUS[0]


def run_bg(work: Callable[[], Any], done: Callable[[Any], None]) -> None:
    """Run work() off the main loop and hand its result to done() on it."""
    def go() -> None:
        try:
            res = work()
        except Exception as exc:   # shown to the owner in plain words, never a traceback
            res = {"ok": False, "message": f"Something went wrong: {exc}"}
        GLib.idle_add(lambda: (done(res), False)[1])
    threading.Thread(target=go, daemon=True).start()


class State:
    def __init__(self) -> None:
        self.catalog: dict[str, Any] = core.load_catalog()
        self.installed: dict[str, dict[str, str]] = {}
        self.updates: list[dict[str, str]] = []
        self.paused: list[str] = []
        self.history: list[dict[str, Any]] = []
        self.status: dict[str, Any] = {}
        self.busy: dict[str, Optional[float]] = {}    # app id -> progress fraction (None = working)
        self.offline = False                          # Flathub couldn't be reached on the last check


class GetAppsWindow(Adw.ApplicationWindow):
    def __init__(self, app: Adw.Application, page: str = "discover", focus_app: str = "") -> None:
        super().__init__(application=app, title="Get apps")
        self.add_css_class("ga-window")
        self.set_default_size(1120, 760)
        self.fp = core.Flatpak()
        self.st = State()
        self._search_gen = 0
        self._search_timer = 0
        self._cards: dict[str, list[Callable[[], None]]] = {}

        self.toasts = Adw.ToastOverlay()
        view = Adw.ToolbarView()
        self.toasts.set_child(view)
        self.set_content(self.toasts)

        header = Adw.HeaderBar()
        self.search = Gtk.SearchEntry(placeholder_text="Search Flathub — apps, games, tools")
        self.search.add_css_class("ga-search")
        self.search.connect("search-changed", self.on_search_changed)
        header.set_title_widget(self.search)
        refresh = Gtk.Button(icon_name="view-refresh-symbolic", tooltip_text="Check again")
        refresh.add_css_class("flat")
        refresh.connect("clicked", lambda _b: self.reload())
        header.pack_end(refresh)
        view.add_top_bar(header)

        body = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        view.set_content(body)
        body.append(self.build_sidebar())
        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE, hexpand=True, vexpand=True)
        body.append(self.stack)
        self.pages: dict[str, Gtk.Box] = {}
        for key, _title, _icon in PAGES + [("search", "Search", "")]:
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
            box.add_css_class("ga-page")
            scroller = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True)
            clamp = Adw.Clamp(maximum_size=1100, tightening_threshold=900)
            clamp.set_child(box)
            scroller.set_child(clamp)
            self.stack.add_named(scroller, key)
            self.pages[key] = box
        self.select_page(page if page in dict((p[0], p) for p in PAGES) else "discover")
        self.render_all()
        self.reload()
        if focus_app:
            self.search.set_text(focus_app)

    # ------------------------------------------------------------------ sidebar
    def build_sidebar(self) -> Gtk.Widget:
        side = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, width_request=230)
        side.add_css_class("ga-sidebar")
        side.append(label("Get apps", "ga-wordmark"))
        side.append(label("Apps from Flathub, kept safe", "ga-wordmark-sub"))
        self.nav = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE)
        self.nav.add_css_class("ga-nav")
        self.nav_rows: dict[str, Gtk.ListBoxRow] = {}
        self.nav_counts: dict[str, Gtk.Label] = {}
        for key, title, icon in PAGES:
            row = Gtk.ListBoxRow()
            row.page_key = key   # type: ignore[attr-defined]
            box = Gtk.Box(spacing=12)
            img = Gtk.Image.new_from_icon_name(icon)
            box.append(img)
            box.append(label(title))
            spacer = Gtk.Box(hexpand=True)
            box.append(spacer)
            count = label("", "ga-count")
            count.set_visible(False)
            box.append(count)
            row.set_child(box)
            self.nav.append(row)
            self.nav_rows[key] = row
            self.nav_counts[key] = count
        self.nav.connect("row-selected", self.on_nav)
        side.append(self.nav)
        side.append(Gtk.Box(vexpand=True))
        self.foot = Gtk.Box(spacing=6)
        self.foot_dot = label("●", "ga-foot-dot")
        self.foot_text = label("Weekly updates: on", "ga-foot", wrap=True)
        self.foot.append(self.foot_dot)
        self.foot.append(self.foot_text)
        side.append(self.foot)
        return side

    def on_nav(self, _lb: Gtk.ListBox, row: Optional[Gtk.ListBoxRow]) -> None:
        if row is not None:
            if self.search.get_text():
                self.search.set_text("")
            self.stack.set_visible_child_name(row.page_key)   # type: ignore[attr-defined]

    def select_page(self, key: str) -> None:
        self.nav.select_row(self.nav_rows[key])
        self.stack.set_visible_child_name(key)

    # ------------------------------------------------------------------ data
    def reload(self) -> None:
        def work() -> dict[str, Any]:
            fp = self.fp
            ok = fp.available()
            ups = fp.updates("app") if ok else []
            return {"installed": {a["id"]: a for a in fp.installed("app")} if ok else {},
                    "updates": ups, "offline": bool(ok and fp.last_error), "paused": fp.masked() if ok else [],
                    "history": core.read_history(40), "status": core.status(fp)}

        def done(res: dict[str, Any]) -> None:
            self.st.installed = res.get("installed", {})
            self.st.updates = res.get("updates", [])
            self.st.offline = res.get("offline", False)
            self.st.paused = res.get("paused", [])
            self.st.history = res.get("history", [])
            self.st.status = res.get("status", {})
            self.render_all()
        run_bg(work, done)

    def toast(self, text: str) -> None:
        t = Adw.Toast(title=GLib.markup_escape_text(text), timeout=5)
        self.toasts.add_toast(t)

    # ------------------------------------------------------------------ rendering
    def clear(self, key: str) -> Gtk.Box:
        box = self.pages[key]
        while (child := box.get_first_child()) is not None:
            box.remove(child)
        return box

    def render_all(self) -> None:
        self._cards.clear()
        self.render_discover()
        self.render_installed()
        self.render_updates()
        self.render_windows()
        self.render_studio()
        n = len([u for u in self.st.updates if u["id"] not in self.st.paused])
        self.nav_counts["updates"].set_text(str(n))
        self.nav_counts["updates"].set_visible(n > 0)
        ni = len(self.st.installed)
        self.nav_counts["installed"].set_text(str(ni))
        self.nav_counts["installed"].set_visible(False)
        mode = self.st.status.get("auto_update", core.load_config()["auto_update"])
        self.foot_text.set_text({"auto": "Weekly updates on: checked first, then you're told",
                                 "notify": "Weekly check: Orion only tells you what's waiting",
                                 "off": "Weekly updates are off"}[mode])
        self.foot_dot.remove_css_class("off")
        if mode != "auto":
            self.foot_dot.add_css_class("off")

    def action_button(self, app_id: str, name: str, compact: bool = False) -> Gtk.Widget:
        box = Gtk.Box(spacing=6, valign=Gtk.Align.CENTER)
        if app_id in self.st.busy:
            frac = self.st.busy[app_id]
            bar = Gtk.ProgressBar(fraction=frac or 0.0, valign=Gtk.Align.CENTER, width_request=110)
            bar.add_css_class("ga-progress")
            if frac is None:
                bar.pulse()
                GLib.timeout_add(120, lambda: (bar.pulse(), app_id in self.st.busy and bar.get_root() is not None)[1])
            box.append(bar)
            box.append(label("Installing…", "ga-dim"))
            return box
        if app_id in self.st.installed:
            box.append(button("Open", "ga-quiet", lambda _b: self.launch(app_id, name)))
            if not compact:
                box.append(button("Remove", "ga-quiet ga-danger", lambda _b: self.confirm_remove(app_id, name)))
        else:
            box.append(button("Install", "suggested-action ga-install", lambda _b: self.install(app_id, name)))
        return box

    def card(self, pick: dict[str, Any]) -> Gtk.Widget:
        c = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, width_request=180)
        c.add_css_class("ga-card")
        top = Gtk.Box(spacing=12)
        top.append(app_icon(pick["id"], pick.get("icon", "")))
        names = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, valign=Gtk.Align.CENTER, spacing=2)
        names.append(label(pick["name"], "ga-name", lines=1))
        names.append(label(pick.get("category", ""), "ga-dim"))
        top.append(names)
        c.append(top)
        summary = label(pick.get("summary", ""), "ga-summary", wrap=True, lines=2)
        summary.set_vexpand(True)
        summary.set_valign(Gtk.Align.START)
        c.append(summary)
        bottom = Gtk.Box(spacing=6)
        if pick["id"] in self.st.installed:
            bottom.append(badge("INSTALLED", "installed"))
        else:
            bottom.append(badge("OPEN SOURCE", "open") if pick.get("open_source") else badge("PROPRIETARY", "closed"))
        bottom.append(Gtk.Box(hexpand=True))
        bottom.append(self.action_button(pick["id"], pick["name"], compact=True))
        c.append(bottom)
        return c

    def render_discover(self) -> None:
        box = self.clear("discover")
        hero = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        hero.add_css_class("ga-hero")
        hero.append(label("Apps for OrionOS", "ga-h1"))
        hero.append(label("Hand-picked from Flathub, sandboxed, and one click to install. Every update is checked "
                          "first: if an app stops starting, Orion puts the old version back.", "ga-lead", wrap=True))
        if not self.st.status.get("flathub", True):
            hero.append(label("Flathub isn't set up on this computer yet.", "ga-dim"))
        box.append(hero)
        cats = self.st.catalog.get("categories", [])
        picks = self.st.catalog.get("picks", [])
        for cat in cats:
            items = [p for p in picks if p.get("category") == cat]
            if not items:
                continue
            box.append(label(cat.upper(), "ga-h2"))
            # The same four columns in every section, so cards line up from one section to the next.
            flow = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, homogeneous=True, column_spacing=12,
                               row_spacing=12, min_children_per_line=4, max_children_per_line=4)
            for p in items:
                flow.append(self.card(p))
            box.append(flow)

    def row(self, app_id: str, name: str, detail: str, right: Gtk.Widget, hint: str = "",
            badges: tuple[Gtk.Widget, ...] = ()) -> Gtk.Widget:
        r = Gtk.Box(spacing=14)
        r.add_css_class("ga-row")
        r.append(app_icon(app_id, hint, 40))
        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, valign=Gtk.Align.CENTER, hexpand=True)
        head = Gtk.Box(spacing=8)
        head.append(label(name, "ga-name"))
        for b in badges:
            head.append(b)
        text.append(head)
        text.append(label(detail, "ga-dim", wrap=True, lines=2))
        r.append(text)
        r.append(right)
        return r

    def render_installed(self) -> None:
        box = self.clear("installed")
        box.append(label("Installed", "ga-h1"))
        box.append(label("Apps from Flathub on this computer, for everyone who uses it.", "ga-lead"))
        box.append(Gtk.Box(height_request=10))
        if not self.st.installed:
            box.append(label("No apps installed yet. Discover has Orion's picks.", "ga-empty", xalign=0.5))
            return
        for app_id, a in sorted(self.st.installed.items(), key=lambda kv: kv[1].get("name", "").lower()):
            pick = core.pick_by_id(app_id, self.st.catalog) or {}
            paused = app_id in self.st.paused
            detail = " · ".join(x for x in (a.get("version") and f"Version {a['version']}", app_id, a.get("size")) if x)
            box.append(self.row(app_id, a.get("name") or app_id, detail, self.action_button(app_id, a.get("name") or app_id),
                                pick.get("icon", ""), (badge("UPDATES PAUSED", "paused"),) if paused else ()))

    def render_updates(self) -> None:
        box = self.clear("updates")
        box.append(label("Updates", "ga-h1"))
        safe = Gtk.Box(spacing=16)
        safe.add_css_class("ga-safe")
        txt = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, hexpand=True)
        txt.append(label("Safe updates", "ga-name"))
        txt.append(label("Orion notes each app's current version, updates it, then checks it still starts. If it "
                         "doesn't, the old version goes back and that app's updates are paused until you say so.",
                         "ga-dim", wrap=True))
        safe.append(txt)
        waiting = [u for u in self.st.updates if u["id"] not in self.st.paused]
        self.update_btn = button("Update all safely", "suggested-action ga-install", lambda _b: self.update_all())
        self.update_btn.set_sensitive(bool(waiting) and "__all__" not in self.st.busy)
        if "__all__" in self.st.busy:
            self.update_btn.set_label("Updating…")
        safe.append(self.update_btn)
        box.append(safe)
        box.append(label(f"WAITING ({len(waiting)})", "ga-h2"))
        if self.st.offline:
            box.append(label("Orion couldn't reach Flathub to check for updates. Is the internet connected?",
                             "ga-dim", wrap=True))
        elif not waiting:
            box.append(label("Every app is up to date.", "ga-dim"))
        for u in waiting:
            name = u.get("name") or u["id"]
            have = self.st.installed.get(u["id"], {}).get("version", "")
            detail = (f"{have} → {u['version']}" if have and u.get("version") else u.get("version") or u["id"])
            if u.get("download_size"):
                detail += f" · {u['download_size']} download"
            pick = core.pick_by_id(u["id"], self.st.catalog) or {}
            box.append(self.row(u["id"], name, detail, Gtk.Box(), pick.get("icon", "")))
        if self.st.paused:
            box.append(label("PAUSED AFTER A BAD UPDATE", "ga-h2"))
            for p in self.st.paused:
                name = self.st.installed.get(p, {}).get("name") or core._name(p)
                right = button("Let it update again", "ga-quiet", lambda _b, p=p: self.resume(p))
                box.append(self.row(p, name, "Its last update didn't start, so the previous version was put back.",
                                    right, badges=(badge("PAUSED", "paused"),)))
        box.append(label("WHAT CHANGED", "ga-h2"))
        if not self.st.history:
            box.append(label("Nothing yet. Every install, update and rollback is written down here.", "ga-dim"))
        for c in self.st.history[:12]:
            good = c.get("result") in ("updated", "installed", "removed", "resumed")
            when = time.strftime("%a %d %b, %H:%M", time.localtime(c.get("ts", 0)))
            what = {"app_update": "Updated", "app_install": "Installed", "app_remove": "Removed",
                    "app_rollback": "Put the old version back", "app_update_failed": "Update failed",
                    "runtime_update": "Updated shared app parts", "runtime_rollback": "Put old shared parts back",
                    "app_resume": "Updates resumed"}.get(c.get("kind", ""), c.get("kind", ""))
            line = Gtk.Box(spacing=10)
            line.append(label("●", "ga-history-ok" if good else "ga-history-bad"))
            line.append(label(f"{what}: {c.get('name') or c.get('app') or ''}", "ga-name" if not good else ""))
            line.append(label(c.get("why", ""), "ga-dim", lines=1))
            line.append(Gtk.Box(hexpand=True))
            line.append(label(when, "ga-mono"))
            box.append(line)

    def render_windows(self) -> None:
        """Run Windows apps: Bottles, Steam (Proton), Lutris, and OrionOS's recipes (catalog.json "windows")."""
        box = self.clear("windows")
        win = self.st.catalog.get("windows", {})
        box.append(label(win.get("title", "Run Windows apps"), "ga-h1"))
        box.append(label(win.get("about", ""), "ga-lead", wrap=True))
        if win.get("caution"):
            caution = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
            caution.add_css_class("ga-caution")
            caution.append(label("GOOD TO KNOW", "ga-caution-title"))
            caution.append(label(win["caution"], "", wrap=True))
            box.append(Gtk.Box(height_request=6))
            box.append(caution)
        box.append(label("APPS", "ga-h2"))
        for a in win.get("apps", []):
            box.append(self.row(a["id"], a["name"], a.get("use", ""), self.action_button(a["id"], a["name"])))
        if win.get("recipes"):
            box.append(label("RECIPES", "ga-h2"))
        for r in win.get("recipes", []):
            run = button("Open", "ga-quiet", lambda _b, r=r: self.in_terminal(r["command"]),
                         f"Runs {r['command']} in a terminal window")
            box.append(self.row("", r["name"], f"{r.get('about', '')} ({r['command']})", run,
                                "utilities-terminal"))

    def in_terminal(self, command: str) -> None:
        try:
            subprocess.Popen(["foot", "--title=orion-tool", "sh", "-c",
                              f'{command}; printf "\\nPress Enter to close"; read _'],
                             start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            self.toast("Opened in a terminal window.")
        except OSError:
            self.toast(f"Couldn't open a terminal. Run it yourself: {command}")

    def render_studio(self) -> None:
        box = self.clear("studio")
        studio = self.st.catalog.get("studio", {})
        head = Gtk.Box(spacing=10)
        head.append(label(studio.get("title", "Orion Studio"), "ga-h1"))
        head.append(badge("OPTIONAL", ""))
        box.append(head)
        box.append(label(studio.get("about", ""), "ga-lead", wrap=True))
        caution = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        caution.add_css_class("ga-caution")
        caution.append(label("BEFORE YOU INSTALL", "ga-caution-title"))
        caution.append(label(studio.get("caution", ""), "", wrap=True))
        box.append(Gtk.Box(height_request=6))
        box.append(caution)
        box.append(label(studio.get("how_to_install", ""), "ga-dim", wrap=True))
        ready = [a for a in studio.get("apps", []) if a.get("flatpak_id")]
        soon = [a for a in studio.get("apps", []) if a.get("coming_soon")]
        box.append(label("AVAILABLE NOW", "ga-h2"))
        for a in ready:
            fid = a["flatpak_id"]
            right = Gtk.Box(spacing=6, valign=Gtk.Align.CENTER)
            if fid in self.st.installed:
                right.append(button("Open", "ga-quiet", lambda _b, a=a: self.launch(a["flatpak_id"], a["name"])))
            else:
                right.append(button("Download page", "ga-quiet", lambda _b, a=a: self.open_url(a["url"]),
                                    "Opens the app's official release page in your browser"))
                right.append(button("Install downloaded file…", "suggested-action ga-install",
                                    lambda _b, a=a: self.pick_bundle(a)))
            detail = f"{a['like']}. {a.get('source', '')}. {a.get('agent_hooks', '')}.".strip()
            box.append(self.row(fid, a["name"], detail, right, a.get("icon", ""),
                                (badge(a.get("status", "alpha").upper(), "alpha"),)))
        box.append(label("COMING SOON", "ga-h2"))
        flow = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, homogeneous=True, column_spacing=10,
                           row_spacing=10, min_children_per_line=2, max_children_per_line=4)
        for a in soon:
            c = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
            c.add_css_class("ga-card")
            top = Gtk.Box(spacing=8)
            top.append(label(a["name"], "ga-name", lines=1, wrap=True))
            top.append(Gtk.Box(hexpand=True))
            top.append(badge("SOON", "soon"))
            c.append(top)
            c.append(label(a["like"], "ga-summary", wrap=True, lines=2))
            link = Gtk.LinkButton(uri=a["url"], label="On GitHub", halign=Gtk.Align.START)
            link.add_css_class("flat")
            c.append(link)
            flow.append(c)
        box.append(flow)

    def render_search(self, words: str, results: list[dict[str, str]], done: bool) -> None:
        box = self.clear("search")
        box.append(label(f"Results for “{words}”", "ga-h1"))
        if not done:
            box.append(label("Searching Flathub…", "ga-dim"))
            return
        if not results:
            box.append(label("Nothing on Flathub matches that. Try another word, or check the spelling.",
                             "ga-empty", xalign=0.5, wrap=True))
            return
        box.append(label(f"{len(results)} app{'s' if len(results) != 1 else ''} on Flathub", "ga-dim"))
        box.append(Gtk.Box(height_request=8))
        for r in results[:60]:
            pick = core.pick_by_id(r["id"], self.st.catalog)
            badges: tuple[Gtk.Widget, ...] = (badge("ORION PICK", "soon"),) if pick else ()
            box.append(self.row(r["id"], r.get("name") or r["id"], f"{r.get('summary', '')} · {r['id']}",
                                self.action_button(r["id"], r.get("name") or r["id"]),
                                (pick or {}).get("icon", ""), badges))

    # ------------------------------------------------------------------ search
    def on_search_changed(self, entry: Gtk.SearchEntry) -> None:
        words = entry.get_text().strip()
        if self._search_timer:
            GLib.source_remove(self._search_timer)
            self._search_timer = 0
        if not words:
            row = self.nav.get_selected_row()
            self.stack.set_visible_child_name(row.page_key if row else "discover")   # type: ignore[attr-defined]
            return
        self.render_search(words, [], False)
        self.stack.set_visible_child_name("search")
        self._search_timer = GLib.timeout_add(300, self.run_search, words)

    def run_search(self, words: str) -> bool:
        self._search_timer = 0
        self._search_gen += 1
        gen = self._search_gen
        self._last_search = words
        run_bg(lambda: self.fp.search(words[:100]),
               lambda res: gen == self._search_gen and self.render_search(words, res, True))
        return False

    def rerender_search(self) -> None:
        words = self.search.get_text().strip()
        if words:
            self.run_search(words)

    # ------------------------------------------------------------------ actions (the owner's clicks)
    def install(self, app_id: str, name: str) -> None:
        self.st.busy[app_id] = None
        self.render_all()
        self.rerender_search()

        def progress(pct: Optional[int], _line: str) -> None:
            if pct is not None:
                GLib.idle_add(self._progress, app_id, pct / 100.0)

        def done(res: dict[str, Any]) -> None:
            self.st.busy.pop(app_id, None)
            self.toast(res.get("message", ""))
            self.reload()
            self.rerender_search()
        run_bg(lambda: core.act_install(self.fp, app_id, progress=progress), done)

    def _progress(self, app_id: str, frac: float) -> bool:
        if app_id not in self.st.busy:
            return False
        old = self.st.busy[app_id]
        self.st.busy[app_id] = frac
        if old is None or int(frac * 20) != int(old * 20):   # redraw every 5 %
            self.render_all()
        return False

    def confirm_remove(self, app_id: str, name: str) -> None:
        body = (f"{name} is removed for everyone on this computer. Its settings and files stay in "
                f"~/.var/app/{app_id}, so reinstalling picks up where you left off.")
        if hasattr(Adw, "AlertDialog"):
            dlg = Adw.AlertDialog(heading=f"Remove {name}?", body=body)
        else:
            dlg = Adw.MessageDialog(transient_for=self, heading=f"Remove {name}?", body=body)
        dlg.add_response("cancel", "Keep it")
        dlg.add_response("remove", "Remove")
        dlg.set_response_appearance("remove", Adw.ResponseAppearance.DESTRUCTIVE)
        dlg.set_default_response("cancel")

        def on_response(_d: Any, resp: str) -> None:
            if resp == "remove":
                run_bg(lambda: core.act_remove(self.fp, app_id),
                       lambda res: (self.toast(res.get("message", "")), self.reload()))
        dlg.connect("response", on_response)
        if hasattr(Adw, "AlertDialog") and isinstance(dlg, Adw.AlertDialog):
            dlg.present(self)
        else:
            dlg.present()

    def update_all(self) -> None:
        self.st.busy["__all__"] = None
        self.render_updates()

        def done(res: dict[str, Any]) -> None:
            self.st.busy.pop("__all__", None)
            self.toast(res.get("summary") or res.get("message", ""))
            self.reload()
        run_bg(lambda: core.update_all(self.fp, mode="manual"), done)

    def resume(self, pattern: str) -> None:
        run_bg(lambda: core.act_resume(self.fp, pattern), lambda res: (self.toast(res["message"]), self.reload()))

    def launch(self, app_id: str, name: str) -> None:
        try:
            subprocess.Popen([self.fp.binary, "run", app_id], start_new_session=True,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            self.toast(f"Opening {name}…")
        except OSError:
            self.toast(f"{name} couldn't be opened.")

    def open_url(self, url: str) -> None:
        try:
            Gtk.UriLauncher.new(url).launch(self, None, None, None)
        except Exception:
            subprocess.Popen(["xdg-open", url], start_new_session=True)

    def pick_bundle(self, app: dict[str, Any]) -> None:
        dlg = Gtk.FileDialog(title=f"Pick the {app['name']} .flatpak you downloaded")
        filt = Gtk.FileFilter()
        filt.set_name("Flatpak bundles")
        filt.add_pattern("*.flatpak")
        store = Gio.ListStore.new(Gtk.FileFilter)
        store.append(filt)
        dlg.set_filters(store)
        downloads = GLib.get_user_special_dir(GLib.UserDirectory.DIRECTORY_DOWNLOAD)
        if downloads:
            dlg.set_initial_folder(Gio.File.new_for_path(downloads))

        def chosen(d: Gtk.FileDialog, result: Gio.AsyncResult) -> None:
            try:
                f = d.open_finish(result)
            except GLib.Error:
                return
            if f is None or not f.get_path():
                return
            self.st.busy[app["flatpak_id"]] = None
            run_bg(lambda: core.act_install_bundle(self.fp, f.get_path()),
                   lambda res: (self.st.busy.pop(app["flatpak_id"], None), self.toast(res["message"]), self.reload()))
        dlg.open(self, None, chosen)


class GetApps(Adw.Application):
    def __init__(self, page: str, focus_app: str) -> None:
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.NON_UNIQUE
                         if os.environ.get("ORION_GETAPPS_NON_UNIQUE") else Gio.ApplicationFlags.DEFAULT_FLAGS)
        self.page, self.focus_app = page, focus_app

    def do_startup(self) -> None:
        Adw.Application.do_startup(self)
        Adw.StyleManager.get_default().set_color_scheme(Adw.ColorScheme.FORCE_DARK)
        settings = Gtk.Settings.get_default()
        if settings is not None:
            settings.set_property("gtk-decoration-layout", "close,minimize,maximize:minimize,maximize,close")
        display = Gdk.Display.get_default()
        for path in (first_existing(COLOUR_CSS), first_existing(THEME_CSS), HERE / "getapps.css"):
            if path is not None and path.is_file():
                prov = Gtk.CssProvider()
                prov.load_from_path(str(path))
                Gtk.StyleContext.add_provider_for_display(display, prov, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

    def do_activate(self) -> None:
        win = self.props.active_window or GetAppsWindow(self, self.page, self.focus_app)
        win.present()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--page", default="discover")
    ap.add_argument("--app", default="")
    a = ap.parse_args()
    GLib.set_prgname(APP_ID)        # the Wayland app_id (Sway rules and the taskbar match on it)
    GLib.set_application_name("Get apps")
    return GetApps(a.page, a.app).run([sys.argv[0]])


if __name__ == "__main__":
    sys.exit(main())
