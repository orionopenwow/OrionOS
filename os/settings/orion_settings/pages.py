"""The Settings pages. Each page loads its data in the background (System, backend.py), then builds its rows.
Labels are plain words; nothing here names the owner."""
from __future__ import annotations

import shlex
from typing import TYPE_CHECKING, Any, Callable, Optional, Sequence

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk  # noqa: E402

from . import admin as A  # noqa: E402
from . import everyone as EV  # noqa: E402
from . import parsers as P  # noqa: E402

if TYPE_CHECKING:
    from .ui import SettingsWindow

NOT_INSTALLED = "Not installed on this computer yet."


def esc(text: str) -> str:
    """Row titles are Pango markup: escape &, < and > (apostrophes and quotes stay as they are everywhere)."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


class Page:
    key = ""
    title = ""
    icon = "emblem-system-symbolic"
    section = "System"
    blurb = ""

    def __init__(self, win: "SettingsWindow") -> None:
        self.win = win
        self.sys = win.system
        self.widget = Adw.PreferencesPage()
        self.groups: list[Adw.PreferencesGroup] = []
        self._busy = False
        self.flags: dict[str, Any] = {}

    # lifecycle ---------------------------------------------------------------------------------
    def refresh(self, **flags: Any) -> None:
        from .ui import in_background
        self.flags = flags
        if not self.groups:
            self._intro()
            spin = Adw.PreferencesGroup()
            sp = Gtk.Spinner(spinning=True, halign=Gtk.Align.CENTER, margin_top=40)
            sp.set_size_request(28, 28)
            spin.add(sp)
            self._add(spin)
        in_background(self.load, self._loaded)

    def load(self) -> Any:
        return None

    def _loaded(self, data: Any) -> None:
        for g in self.groups:
            self.widget.remove(g)
        self.groups = []
        self._intro()
        if isinstance(data, Exception):
            g = self.group()
            g.add(self.row("Couldn't read these settings", "Try again with the refresh button. If it keeps "
                                                           "happening, restart the computer.",
                           icon="dialog-warning-symbolic"))
            return
        self.fill(data)

    def fill(self, data: Any) -> None:
        pass

    # building blocks ---------------------------------------------------------------------------
    def _add(self, g: Adw.PreferencesGroup) -> Adw.PreferencesGroup:
        self.widget.add(g)
        self.groups.append(g)
        return g

    def _intro(self) -> None:
        g = Adw.PreferencesGroup()
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, margin_bottom=4)
        t = Gtk.Label(label=self.title, xalign=0)
        t.add_css_class("orion-page-title")
        box.append(t)
        if self.blurb:
            s = Gtk.Label(label=self.blurb, xalign=0, wrap=True)
            s.add_css_class("orion-page-sub")
            box.append(s)
        g.add(box)
        self._add(g)

    def group(self, title: str = "", desc: str = "", suffix: Optional[Gtk.Widget] = None) -> Adw.PreferencesGroup:
        g = Adw.PreferencesGroup(title=esc(title) if title else "")
        if desc:
            g.set_description(desc)
        if suffix is not None:
            g.set_header_suffix(suffix)
        return self._add(g)

    @staticmethod
    def row(title: str, subtitle: str = "", icon: Optional[str] = None, suffix: Sequence[Gtk.Widget] = (),
            activatable: Optional[Gtk.Widget] = None) -> Adw.ActionRow:
        r = Adw.ActionRow(title=esc(title))
        if subtitle:
            r.set_subtitle(esc(subtitle))
        if icon:
            r.add_prefix(Gtk.Image.new_from_icon_name(icon))
        for w in suffix:
            r.add_suffix(w)
        if activatable is not None:
            r.set_activatable_widget(activatable)
        return r

    @staticmethod
    def pill(text: str, kind: str = "") -> Gtk.Label:
        lab = Gtk.Label(label=text, valign=Gtk.Align.CENTER)
        lab.add_css_class("orion-pill")
        if kind:
            lab.add_css_class(kind)
        return lab

    def button(self, label: str, cb: Callable[[], None], style: str = "", icon: Optional[str] = None,
               sensitive: bool = True) -> Gtk.Button:
        b = Gtk.Button(label=label, valign=Gtk.Align.CENTER, sensitive=sensitive)
        if icon:
            b.set_child(Adw.ButtonContent(icon_name=icon, label=label))
        b.add_css_class("pill-small")
        if style:
            b.add_css_class(style)
        b.connect("clicked", lambda _b: cb())
        return b

    def switch(self, g: Adw.PreferencesGroup, title: str, subtitle: str, active: bool,
               change: Callable[[bool], tuple[bool, str]], sensitive: bool = True) -> Adw.SwitchRow:
        r = Adw.SwitchRow(title=esc(title), active=bool(active), sensitive=sensitive)
        if subtitle:
            r.set_subtitle(esc(subtitle))
        r.connect("notify::active", lambda row, _p: self.win.act(lambda: change(row.get_active()), after=self.refresh))
        g.add(r)
        return r

    def combo(self, g: Adw.PreferencesGroup, title: str, subtitle: str, options: Sequence[tuple[str, str]],
              current: Optional[str], change: Callable[[str], tuple[bool, str]],
              sensitive: bool = True) -> Adw.ComboRow:
        r = Adw.ComboRow(title=esc(title), sensitive=sensitive)
        if subtitle:
            r.set_subtitle(esc(subtitle))
        r.set_model(Gtk.StringList.new([lab for _v, lab in options]))
        values = [v for v, _l in options]
        if current in values:
            r.set_selected(values.index(current))

        def picked(row: Adw.ComboRow, _p: Any) -> None:
            v = values[row.get_selected()]
            if v != current:
                self.win.act(lambda: change(v), after=self.refresh)
        r.connect("notify::selected", picked)
        g.add(r)
        return r

    def missing(self, g: Adw.PreferencesGroup, title: str, tool: str) -> None:
        g.add(self.row(title, NOT_INSTALLED, icon="action-unavailable-symbolic", suffix=[self.pill("not installed", "off")]))

    def terminal_button(self, label: str, command: str, style: str = "") -> Gtk.Button:
        return self.button(label, lambda: self.win.toast("Opened in a terminal window." if
                                                         self.sys.terminal(command) else
                                                         "Couldn't open a terminal window."), style=style)

    def launch_button(self, label: str, argv: list[str], style: str = "") -> Gtk.Button:
        return self.button(label, lambda: self.win.toast("" if self.sys.launch(argv) else
                                                         f"Couldn't open {label.lower()}."), style=style)


# ============================================================================================ Wi-Fi & network
class NetworkPage(Page):
    key, title, icon, section = "network", "Wi-Fi & network", "network-wireless-symbolic", "Connections"
    blurb = "Join a Wi-Fi network, check your connection, or switch everything off for a flight."

    def load(self) -> Any:
        d = self.sys.network(rescan=bool(self.flags.get("rescan")))
        d["share"] = self.sys.has("orion-share")
        d["vm"] = (self.sys.r.read(self.sys.root + "/etc/orion/profile") or "").strip() == "vm"
        return d

    def fill(self, d: dict[str, Any]) -> None:
        if not d.get("available"):
            g = self.group()
            if d.get("vm"):
                g.add(self.row("Wi-Fi is your PC's job here", "OrionOS is running in a virtual machine: it uses "
                                                              "your PC's connection.", icon="computer-symbolic"))
            else:
                g.add(self.row("Network settings aren't available", d.get("error") or NOT_INSTALLED,
                               icon="network-offline-symbolic"))
            self._more(d)
            return
        g = self.group("Wi-Fi")
        self.switch(g, "Wi-Fi", "Find and join wireless networks", d["wifi_on"], self.sys.set_wifi,
                    sensitive=d["has_wifi"])
        self.switch(g, "Airplane mode", "Turns off Wi-Fi and Bluetooth", d["airplane"], self.sys.set_airplane)

        wired = [x for x in d["devices"] if x["type"] == "ethernet"]
        if wired:
            wg = self.group("Cable")
            for w in wired:
                on = w["state"].startswith("connected")
                wg.add(self.row("Network cable", "Connected" if on else "Not plugged in",
                                icon="network-wired-symbolic", suffix=[self.pill("on" if on else "off",
                                                                                 "ok" if on else "off")]))
        if not d["has_wifi"]:
            g.add(self.row("No Wi-Fi adapter found", "Plug in a network cable, or a USB Wi-Fi adapter.",
                           icon="network-wireless-offline-symbolic"))
        elif d["wifi_on"]:
            look = self.button("Look again", lambda: self.refresh(rescan=True), style="flat",
                               icon="view-refresh-symbolic")
            ng = self.group("Networks nearby", "Pick one to join. Your password stays on this computer.",
                            suffix=look)
            if not d["networks"]:
                ng.add(self.row("No networks found", "Move closer to the router, or look again."))
            wifi_dev = next((x["device"] for x in d["devices"] if x["type"] == "wifi"), "")
            for n in d["networks"][:20]:
                ng.add(self._net_row(n, wifi_dev))
        self._more(d)

    def _net_row(self, n: dict[str, Any], dev: str) -> Adw.ActionRow:
        strength = "excellent" if n["signal"] >= 70 else "good" if n["signal"] >= 45 else \
            "ok" if n["signal"] >= 25 else "weak"
        bits = []
        if n["in_use"]:
            bits.append("Connected")
        elif n.get("saved"):
            bits.append("Saved")
        bits.append(P.wifi_bars(n["signal"]))
        bits.append("Work or school sign-in" if n["enterprise"] else "Password" if n["security"] else "Open, no password")
        suffix: list[Gtk.Widget] = []
        if n["security"]:
            suffix.append(Gtk.Image.new_from_icon_name("system-lock-screen-symbolic"))
        if n.get("saved"):
            suffix.append(self.button("Forget", lambda s=n["ssid"]: self._forget(s), style="flat"))
        if n["in_use"]:
            suffix.append(self.button("Disconnect", lambda: self.win.act(lambda: self.sys.wifi_disconnect(dev),
                                                                         after=self.refresh)))
        else:
            suffix.append(self.button("Connect", lambda n=n: self._connect(n), style="suggested-action"))
        r = self.row(n["ssid"], " · ".join(bits), icon=f"network-wireless-signal-{strength}-symbolic", suffix=suffix)
        if n["in_use"]:
            r.add_css_class("orion-current")
        return r

    def _forget(self, ssid: str) -> None:
        self.win.confirm(f"Forget {ssid}?", "This computer stops joining it by itself. You'll need the password "
                         "again to join.", "Forget", lambda: self.win.act(lambda: self.sys.wifi_forget(ssid),
                                                                        after=self.refresh), destructive=True)

    def _connect(self, n: dict[str, Any]) -> None:
        ssid, sec = n["ssid"], n["security"]
        if n["enterprise"]:
            self.win.toast("This network needs a work or school sign-in. Use 'More network options'.", long=True)
            return
        if n.get("saved") or not sec:
            self.win.toast(f"Connecting to {ssid}…")
            self.win.act(lambda: self.sys.wifi_connect(ssid, None, sec), after=self.refresh)
            return
        box = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        box.add_css_class("boxed-list")
        entry = Adw.PasswordEntryRow(title="Password")
        box.append(entry)

        def go() -> None:
            pw = entry.get_text()
            entry.set_text("")
            self.win.toast(f"Connecting to {ssid}…")
            self.win.act(lambda: self.sys.wifi_connect(ssid, pw, sec), after=self.refresh)
        entry.connect("entry-activated", lambda _e: None)
        self.win.confirm(f"Join {ssid}", "Type the network's password. It's saved on this computer only, "
                         "readable by the system alone.", "Connect", go, extra=box)

    def _more(self, d: dict[str, Any]) -> None:
        g = self.group("More")
        if self.sys.has("nmtui"):
            g.add(self.row("More network options", "VPNs, hidden networks, work or school sign-in",
                           icon="preferences-system-network-symbolic",
                           suffix=[self.terminal_button("Open", "nmtui")]))
        if d.get("share"):
            g.add(self.row("Share files nearby", "Send files to your phone or another computer on this network",
                           icon="send-to-symbolic", suffix=[self.launch_button("Open", ["orion-share", "open"])]))
        else:
            self.missing(g, "Share files nearby", "orion-share")


# ============================================================================================ Bluetooth
class BluetoothPage(Page):
    key, title, icon, section = "bluetooth", "Bluetooth", "bluetooth-active-symbolic", "Connections"
    blurb = "Headphones, keyboards, mice, speakers and controllers without a cable."

    def load(self) -> Any:
        return self.sys.bluetooth(scan=bool(self.flags.get("scan")))

    def fill(self, d: dict[str, Any]) -> None:
        if not d.get("available"):
            g = self.group()
            g.add(self.row("No Bluetooth adapter found",
                           "This computer has no Bluetooth, or it's switched off in airplane mode." if d.get("tool")
                           else NOT_INSTALLED, icon="bluetooth-disabled-symbolic"))
            self._more(d)
            return
        g = self.group()
        self.switch(g, "Bluetooth", f"Visible to your devices as “{d['name']}”" if d["name"] else "", d["powered"],
                    self.sys.bt_power)
        if d["powered"]:
            search = self.button("Searching…" if self.flags.get("scan") else "Search",
                                 lambda: (self.win.toast("Looking for devices for 8 seconds. Put yours in pairing "
                                                         "mode."), self.refresh(scan=True)),
                                 style="flat", icon="system-search-symbolic")
            dg = self.group("Devices", "Put a new device in pairing mode, then search.", suffix=search)
            if not d["devices"]:
                dg.add(self.row("No devices yet", "Search to find devices nearby."))
            for dev in d["devices"]:
                dg.add(self._dev_row(dev))
        self._more(d)

    def _dev_row(self, dev: dict[str, Any]) -> Adw.ActionRow:
        bits = ["Connected" if dev["connected"] else "Paired" if dev["paired"] else "Not paired", dev["kind"]]
        if dev.get("battery") is not None:
            bits.append(f"Battery {dev['battery']}%")
        mac = dev["address"]
        act = lambda a: self.win.act(lambda: self.sys.bt_action(a, mac), after=self.refresh)  # noqa: E731
        suffix: list[Gtk.Widget] = []
        if dev["paired"]:
            suffix.append(self.button("Forget", lambda: self.win.confirm(
                f"Forget {dev['name']}?", "You'll need to pair it again to use it.", "Forget",
                lambda: act("remove"), destructive=True), style="flat"))
            suffix.append(self.button("Disconnect", lambda: act("disconnect")) if dev["connected"] else
                          self.button("Connect", lambda: act("connect"), style="suggested-action"))
        else:
            suffix.append(self.button("Pair", lambda: (self.win.toast(f"Pairing with {dev['name']}…"), act("pair")),
                                      style="suggested-action"))
        icon = {"Headset": "audio-headset-symbolic", "Headphones": "audio-headphones-symbolic",
                "Speaker": "audio-speakers-symbolic", "Keyboard": "input-keyboard-symbolic",
                "Mouse": "input-mouse-symbolic", "Game controller": "input-gaming-symbolic",
                "Phone": "phone-symbolic"}.get(dev["kind"], "bluetooth-active-symbolic")
        r = self.row(dev["name"], " · ".join(bits), icon=icon, suffix=suffix)
        if dev["connected"]:
            r.add_css_class("orion-current")
        return r

    def _more(self, d: dict[str, Any]) -> None:
        if d.get("blueman"):
            g = self.group("More")
            g.add(self.row("More Bluetooth options", "Pairing codes, file sending and device details",
                           icon="preferences-system-symbolic",
                           suffix=[self.launch_button("Open", ["blueman-manager"])]))


# ============================================================================================ Sound
class SoundPage(Page):
    key, title, icon, section = "sound", "Sound", "audio-volume-high-symbolic", "Devices"
    blurb = "Where sound comes out, which microphone listens, and how loud."

    def load(self) -> Any:
        return self.sys.sound()

    def fill(self, d: dict[str, Any]) -> None:
        if not d.get("available"):
            g = self.group()
            g.add(self.row("Sound settings aren't available", d.get("error") or NOT_INSTALLED,
                           icon="audio-volume-muted-symbolic"))
        else:
            self._side(d, "output", "Output", d["sinks"], d["volume"], d["muted"], "Speakers or headphones")
            self._side(d, "input", "Input", d["sources"], d["mic_volume"], d["mic_muted"], "Microphone")
        g = self.group("Sounds")
        if d.get("boot_sound") is None:
            self.missing(g, "Boot sound", "orion-boot-sound")
        else:
            self.switch(g, "Boot sound", "A low swell and a soft chime as Orion starts (follows your volume)",
                        d["boot_sound"], self.sys.set_boot_sound)
        if d.get("mixer"):
            g.add(self.row("More sound options", "Each app's volume, and device profiles",
                           icon="multimedia-volume-control-symbolic",
                           suffix=[self.launch_button("Open", ["pavucontrol"])]))

    def _side(self, d: dict[str, Any], which: str, title: str, nodes: list[dict[str, Any]],
              vol: Optional[float], muted: bool, what: str) -> None:
        g = self.group(title)
        if not nodes:
            g.add(self.row(f"No {what.lower()} found", "", icon="action-unavailable-symbolic"))
            return
        if len(nodes) > 1:
            cur = next((str(n["id"]) for n in nodes if n["default"]), None)
            self.combo(g, "Device", "", [(str(n["id"]), n["name"]) for n in nodes], cur,
                       lambda v: self.sys.set_default_audio(int(v)))
        else:
            g.add(self.row("Device", nodes[0]["name"]))
        scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, 100, 1)
        scale.set_value(round((vol or 0) * 100))
        scale.set_draw_value(False)
        scale.set_hexpand(True)
        scale.set_size_request(240, -1)
        scale.set_valign(Gtk.Align.CENTER)
        pending: dict[str, int] = {}

        def moved(s: Gtk.Scale) -> None:
            if pending.get("id"):
                GLib.source_remove(pending["id"])

            def send() -> bool:
                pending.pop("id", None)
                self.win.act(lambda: self.sys.set_volume(which, s.get_value() / 100))
                return False
            pending["id"] = GLib.timeout_add(200, send)
        scale.connect("value-changed", moved)
        mute = Gtk.ToggleButton(icon_name="audio-volume-muted-symbolic" if which == "output"
                                else "microphone-sensitivity-muted-symbolic", active=muted, valign=Gtk.Align.CENTER,
                                tooltip_text="Mute")
        mute.add_css_class("flat")
        mute.connect("toggled", lambda b: self.win.act(lambda: self.sys.set_mute(which, b.get_active())))
        r = self.row("Volume" if which == "output" else "Microphone level", "Muted" if muted else "",
                     icon="audio-volume-high-symbolic" if which == "output" else "audio-input-microphone-symbolic",
                     suffix=[scale, mute])
        g.add(r)


# ============================================================================================ Displays
class DisplaysPage(Page):
    key, title, icon, section = "displays", "Displays", "video-display-symbolic", "Devices"
    blurb = "Screen size and layout, and a warmer screen after sunset."
    SCALES = [("1", "100%"), ("1.25", "125%"), ("1.5", "150%"), ("1.75", "175%"), ("2", "200%")]

    def load(self) -> Any:
        return self.sys.displays()

    def fill(self, d: dict[str, Any]) -> None:
        g = self.group("Screens", "Bigger makes text and windows larger on that screen. In-between sizes (125%, 150%, "
                                  "175%) are sharp in most apps; a few older apps (X11 apps, some games) can look a "
                                  "little soft at them.")
        if not d["outputs"]:
            g.add(self.row("No screen information", "The screen list comes from the desktop; it isn't answering.",
                           icon="video-display-symbolic"))
        for o in d["outputs"]:
            name = "Built-in screen" if o["name"].startswith(("eDP", "LVDS", "DSI")) else (o["label"] or o["name"])
            sub = f"{o['width']} × {o['height']}" if o["width"] else ""
            if o["refresh"]:
                sub += f" · {o['refresh']} Hz"
            cur = f"{o['scale']:g}"
            opts = list(self.SCALES)
            if cur not in [v for v, _ in opts]:
                opts.append((cur, f"{int(o['scale'] * 100)}%"))
            row = self.combo(g, name, sub, opts, cur, lambda v, n=o["name"]: self.sys.set_scale(n, float(v)))
            row.add_prefix(Gtk.Image.new_from_icon_name("computer-symbolic" if name == "Built-in screen"
                                                        else "video-display-symbolic"))
        lg = self.group("Layout")
        if d["orion_displays"]:
            lg.add(self.row("Arrange screens", "Which screen is where, which one is main, mirror or extend",
                            icon="view-dual-symbolic",
                            suffix=[self.launch_button("Arrange", ["orion-displays", "arrange"], "suggested-action")]))
            for prof in d["profiles"]:
                lg.add(self.row(prof, "Saved screen layout", icon="view-grid-symbolic",
                                suffix=[self.button("Use", lambda p=prof: self.win.act(
                                    lambda: self._apply(p), after=self.refresh))]))
        else:
            self.missing(lg, "Arrange screens (Orion's display tool)", "orion-displays")
            if d["wdisplays"]:
                lg.add(self.row("Screen layout tool", "Position, resolution and rotation",
                                icon="preferences-desktop-display-symbolic",
                                suffix=[self.launch_button("Open", ["wdisplays"])]))
        ng = self.group("Night light")
        nl = d["nightlight"]
        if nl is None:
            self.missing(ng, "Night light", "orion-nightlight")
        elif nl == "unavailable":
            ng.add(self.row("Night light", "This screen can't change its colour (common in virtual machines).",
                            icon="night-light-symbolic", suffix=[self.pill("not available", "off")]))
        else:
            self.switch(ng, "Night light", "Warmer colours after sunset, timed from your time zone (Super+N)",
                        nl in ("on", "waiting"), self.sys.set_nightlight)

    def _apply(self, prof: str) -> tuple[bool, str]:
        from .backend import plain_error
        r = self.sys.r.run(["orion-displays", "apply", prof], timeout=30)
        return (True, f"Using “{prof}”.") if r.ok else (False, plain_error(r, "use that layout"))


# ============================================================================================ Printers
class PrintersPage(Page):
    key, title, icon, section = "printers", "Printers & scanners", "printer-symbolic", "Devices"
    blurb = "Printers and scanners on this computer or your network."

    def load(self) -> Any:
        if not self.sys.has("orion-printers"):
            return None
        data = P.parse_json_maybe(self.sys.r.run(["orion-printers", "list", "--json"], timeout=20).out)
        if isinstance(data, dict):
            data = data.get("printers", [])
        return data if isinstance(data, list) else []

    def fill(self, d: Any) -> None:
        g = self.group("Printers")
        if d is None:
            self.missing(g, "Printers and scanners", "orion-printers")
            return
        if not d:
            g.add(self.row("No printers yet", "Add one, or turn your printer on and check again."))
        for p in d:
            p = p if isinstance(p, dict) else {"name": str(p)}
            sub = " · ".join(str(p[k]) for k in ("state", "location") if p.get(k))
            g.add(self.row(str(p.get("name", "Printer")), sub, icon="printer-symbolic",
                           suffix=[self.pill("default", "ok")] if p.get("default") else []))
        a = self.group()
        a.add(self.row("Add a printer", "Finds printers on your network and USB", icon="list-add-symbolic",
                       suffix=[self.launch_button("Add", ["orion-printers", "add"], "suggested-action")]))
        a.add(self.row("Scan a document", "Opens the scanner app", icon="scanner-symbolic",
                       suffix=[self.launch_button("Scan", ["orion-printers", "scan"])]))


# ============================================================================================ Power
LID_CHOICES = [("suspend", "Sleep"), ("suspend-then-hibernate", "Sleep, then hibernate"), ("lock", "Lock the screen"),
               ("ignore", "Do nothing"), ("poweroff", "Power off")]
KEY_CHOICES = [("poweroff", "Power off"), ("suspend", "Sleep"), ("hibernate", "Hibernate"), ("ignore", "Do nothing")]


class PowerPage(Page):
    key, title, icon, section = "power", "Power", "battery-good-symbolic", "Devices"
    blurb = "Battery, speed, and what happens when you close the lid."

    def load(self) -> Any:
        return {**self.sys.power(), **EV.power(self.sys)}

    def fill(self, d: dict[str, Any]) -> None:
        g = self.group("Battery")
        if not d["batteries"]:
            g.add(self.row("No battery", "This computer runs on mains power.", icon="ac-adapter-symbolic"))
        for b in d["batteries"]:
            words = {"Charging": "Charging", "Discharging": "On battery", "Full": "Full", "Not charging": "Plugged in, "
                     "not charging"}.get(b["status"], b["status"] or "")
            bar = Gtk.LevelBar(min_value=0, max_value=100, value=b["percent"], valign=Gtk.Align.CENTER)
            bar.set_size_request(160, 8)
            lvl = "full" if b["percent"] > 90 else "good" if b["percent"] > 40 else "low" if b["percent"] > 15 \
                else "caution"
            g.add(self.row(f"{b['percent']}%", words, icon=f"battery-{lvl}-symbolic", suffix=[bar]))
        pg = self.group("Speed", "How fast the computer runs, against how long the battery lasts.")
        if not d["profiles"]:
            pg.add(self.row("Power modes aren't available", "This computer doesn't offer power modes.",
                            icon="power-profile-balanced-symbolic"))
        else:
            first: Optional[Gtk.CheckButton] = None
            for name in ("power-saver", "balanced", "performance"):
                if name not in d["profiles"]:
                    continue
                t, sub = P.PROFILE_WORDS[name]
                chk = Gtk.CheckButton(active=d["active"] == name, valign=Gtk.Align.CENTER)
                if first is None:
                    first = chk
                else:
                    chk.set_group(first)
                chk.connect("toggled", lambda c, n=name: c.get_active() and n != d["active"] and
                            self.win.act(lambda: self.sys.set_profile(n), after=self.refresh))
                r = self.row(t, sub, activatable=chk)
                r.add_prefix(chk)
                pg.add(r)
        lg = self.group("Lid and power button")
        lid = d["lid"]
        lid_bat = lid.get("HandleLidSwitch", "suspend")
        self.combo(lg, "When I close the lid", "", LID_CHOICES, lid_bat,
                   lambda v: self.sys.set_logind("HandleLidSwitch", v))
        self.combo(lg, "When I close the lid while plugged in", "", LID_CHOICES,
                   lid.get("HandleLidSwitchExternalPower", lid_bat),
                   lambda v: self.sys.set_logind("HandleLidSwitchExternalPower", v))
        self.combo(lg, "When I press the power button", "", KEY_CHOICES, lid.get("HandlePowerKey", "poweroff"),
                   lambda v: self.sys.set_logind("HandlePowerKey", v))
        lg.add(self.row("Sleep now", "Everything stays open; press a key or open the lid to wake",
                        icon="weather-clear-night-symbolic",
                        suffix=[self.launch_button("Sleep", ["systemctl", "suspend"])]))
        from .everyone_pages import power_extra
        power_extra(self, d)   # battery care: warnings, power saver on battery, sleep then hibernate (step 6)


# ============================================================================================ Time
class TimePage(Page):
    key, title, icon, section = "time", "Time", "preferences-system-time-symbolic", "System"
    blurb = "Your time zone. The clock sets itself from the internet."

    def load(self) -> Any:
        return self.sys.time()

    def fill(self, d: dict[str, Any]) -> None:
        g = self.group()
        if not d.get("available"):
            self.missing(g, "Time zone", "orion-timezone")
            return
        g.add(self.row("Time zone", f"{d['zone']} · {d['clock']}" if d["clock"] else d["zone"],
                       icon="mark-location-symbolic",
                       suffix=[self.button("Change", self._pick, style="suggested-action")]))
        if d["lookup_allowed"]:
            g.add(self.row("Find my time zone online", "Asks Ubuntu's location service once. It sees this "
                           "computer's internet address; nothing else is sent.", icon="find-location-symbolic",
                           suffix=[self.button("Find it", self._lookup)]))
        cg = self.group("Clock")
        sync = "In sync" if d["synced"] else "Not in sync yet"
        cg.add(self.row("Network time", ("On · " + sync) if d["ntp"] else "Off", icon="emblem-synchronizing-symbolic",
                        suffix=[self.pill("on" if d["ntp"] else "off", "ok" if d["ntp"] else "off")]))
        if d["rtc"]:
            cg.add(self.row("Hardware clock", f"Kept in {d['rtc']}" + (" (Windows on the same PC may need a fix; see "
                                                                      "the OrionOS guide)" if d["rtc"] == "UTC" else ""),
                            icon="preferences-system-time-symbolic"))

    def _lookup(self) -> None:
        def found(z: Any) -> None:
            if not isinstance(z, str) or not z:
                self.win.toast("No answer (offline?). Pick it from the list instead.", long=True)
                return
            self.win.confirm(f"Use {z}?", "That's the time zone the location service suggested.", "Use it",
                             lambda: self.win.act(lambda: self.sys.set_zone(z), after=self.refresh))
        from .ui import in_background
        self.win.confirm("Find your time zone online?", "Orion asks Ubuntu's location service (geoip.ubuntu.com) "
                         "once. It sees this computer's internet address; nothing else is sent.", "Look it up",
                         lambda: in_background(self.sys.lookup_zone, found))

    def _pick(self) -> None:
        from .ui import in_background
        in_background(self.sys.zones, self._picker)

    def _picker(self, zones: Any) -> None:
        if isinstance(zones, Exception) or not zones:
            self.win.toast("Couldn't read the list of time zones.", long=True)
            return
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        search = Gtk.SearchEntry(placeholder_text="Type a city or country")
        box.append(search)
        lst = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE)
        lst.add_css_class("boxed-list")
        for z, country in zones:
            r = Adw.ActionRow(title=esc(z.replace("_", " ")),
                              subtitle=esc(country))
            r.zone = z  # type: ignore[attr-defined]
            r.hay = f"{z} {country}".lower().replace("_", " ")  # type: ignore[attr-defined]
            lst.append(r)
        lst.set_filter_func(lambda r: search.get_text().lower().strip() in r.hay)  # type: ignore[attr-defined]
        search.connect("search-changed", lambda _s: lst.invalidate_filter())
        sc = Gtk.ScrolledWindow(min_content_height=320, max_content_height=320)
        sc.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        sc.set_child(lst)
        box.append(sc)

        def use() -> None:
            r = lst.get_selected_row()
            if r is not None:
                self.win.act(lambda: self.sys.set_zone(r.zone), after=self.refresh)  # type: ignore[attr-defined]
        self.win.confirm("Your time zone", "", "Use this one", use, extra=box)


# ============================================================================================ Users
class UsersPage(Page):
    key, title, icon, section = "users", "Users", "system-users-symbolic", "System"
    blurb = "Your account, your password, and how OrionOS starts."

    def load(self) -> Any:
        u = self.sys.user()
        return {**u, "people": EV.people(self.sys), "pk": A.pkexec_ready(self.sys),
                "has_pw": A.has_password(self.sys.r, u["login"])}

    def fill(self, d: dict[str, Any]) -> None:
        g = self.group("You")
        g.add(self.row(d["full"] or d["login"], f"Signed in as {d['login']}", icon="avatar-default-symbolic"))
        e = Adw.EntryRow(title="What Orion calls you (leave empty for just “Morning” / “Evening”)",
                         show_apply_button=True)
        e.set_text(d["greeting_name"])
        e.connect("apply", lambda row: self.win.act(lambda: self.sys.set_greeting_name(row.get_text()),
                                                    after=self.refresh))
        g.add(e)
        pk = d.get("pk")   # H5: the admin password is asked for in the system's password window (admin.py)
        pg = self.group("Password", "Admin passwords are asked for in the system's password window." if pk else
                        "Your password is typed in a terminal window, never in this app.")
        pg.add(self.row("Change my password", "Asks for your current password first", icon="dialog-password-symbolic",
                        suffix=[self.terminal_button("Change", "passwd")]))
        if pk:
            from .desktop_pages import ask_password
            set_btn = self.button("Set", lambda: ask_password(self))
        else:
            set_btn = self.terminal_button("Set", 'echo "Choose the password that unlocks this screen '
                                           '(Super+L or Ctrl+Alt+L locks it)."; su -P - orion-admin -c '
                                           f'"sudo passwd {shlex.quote(d["login"])}" && mkdir -p '
                                           '~/.config/orion && touch ~/.config/orion/lock-ready && echo Done.')
        pg.add(self.row("Set a password", "If you don't have one yet: it protects this computer and unlocks the "
                        "lock screen", icon="dialog-password-symbolic",
                        suffix=[self.terminal_button("Set", "pkexec /opt/orion/privacy/orion-first-password")]))
        pg.add(self.row("Set a password with the admin account",
                        "For a first password, or if you forgot yours (asks for the orion-admin password)",
                        icon="system-lock-screen-symbolic", suffix=[set_btn]))
        lg = self.group("Starting OrionOS")
        sw = Adw.SwitchRow(title="Ask for my password when OrionOS starts",
                           subtitle="The starry login screen. Changing it asks for the admin password" +
                                    ("." if pk else " in a terminal."),
                           active=d["password_login"])
        sw.connect("notify::active", lambda r, _p: self._login_switch(r.get_active(), d))
        lg.add(sw)
        lg.add(self.row("Lock the screen now", "Super+L or Ctrl+Alt+L", icon="system-lock-screen-symbolic",
                        suffix=[self.launch_button("Lock", ["orion-lock"])]))
        from .everyone_pages import users_extra
        users_extra(self, d)   # guest account and "Add a person" (step 6)

    def _login_switch(self, on: bool, d: dict[str, Any]) -> None:
        if not d.get("pk"):
            self.win.toast("Opened in a terminal window." if self.sys.terminal(
                "orion-ctl login " + ("password" if on else "auto")) else "Couldn't open a terminal.")
            return
        if on and d.get("has_pw") is False:
            from .desktop_pages import ask_password
            self.win.toast("Choose your password first: the login screen checks it.")
            ask_password(self, then=lambda: A.login_mode(self.sys, "password"))
            return
        self.win.act(lambda: A.login_mode(self.sys, "password" if on else "auto"), after=self.refresh)


# ============================================================================================ Privacy
class PrivacyPage(Page):
    key, title, icon, section = "privacy", "Privacy", "security-high-symbolic", "System"
    blurb = "What this computer and Orion may see and keep. Everything stays on this computer."

    def load(self) -> Any:
        d = self.sys.privacy()
        from .presence_settings import load as presence_load
        d["presence"] = presence_load(self.sys.r)   # Phase 5: camera presence (daemon /presence)
        return d

    def fill(self, d: dict[str, Any]) -> None:
        g = self.group("Camera")
        self.switch(g, "Camera", "Off by default. Turn it on for video calls; apps can't see it while it's off.",
                    d["camera"], self.sys.set_camera)
        from .presence_pages import presence_group
        presence_group(self, d.get("presence"))   # Phase 5: camera presence and gestures (off by default)
        sg = self.group("Orion")
        self.switch(sg, "Orion reads your screen",
                    "Text on screen is read on this computer so Orion can help with what you're doing. Nothing is "
                    "sent anywhere by this.", d["capture"], self.sys.set_capture)
        cg = self.group("Clipboard")
        self.switch(cg, "Clipboard history", "Keeps your last copies for Super+V. Passwords and keys are never kept.",
                    d["clipboard"], self.sys.set_clipboard)
        cg.add(self.row("Clear clipboard history", "Forgets everything copied so far", icon="edit-clear-all-symbolic",
                        suffix=[self.button("Clear", lambda: self.win.act(self.sys.clear_clipboard))]))
        tg = self.group("Online lookups")
        self.switch(tg, "Allow “Find my time zone online”",
                    "Only runs when you press it, and asks first each time. It sees this computer's internet "
                    "address.", d["timezone_lookup"], lambda on: (self.sys.set_pref("timezone_lookup", on) or True,
                                                                  "Saved."))
        if d["killswitch"]:
            ng = self.group("Network")
            ng.add(self.row("VPN killswitch", "Blocks all internet traffic outside your VPN when it's on",
                            icon="network-vpn-symbolic",
                            suffix=[self.terminal_button("Status", "orion-killswitch status")]))


# ============================================================================================ Updates
class UpdatesPage(Page):
    key, title, icon, section = "updates", "Updates", "software-update-available-symbolic", "System"
    blurb = "Keep OrionOS and your apps up to date. System updates are signed and can roll back by themselves."

    def load(self) -> Any:
        d = self.sys.updates()
        d["pk"] = A.pkexec_ready(self.sys)   # H5: admin password in the password window when there is one
        return d

    def fill(self, d: dict[str, Any]) -> None:
        pk = bool(d.get("pk"))
        g = self.group("OrionOS")
        if d["slot"]:
            g.add(self.row("System updates", "Installed into the spare system copy; if it doesn't start, the "
                           "computer goes back to this one by itself", icon="system-software-update-symbolic",
                           suffix=[self.terminal_button(
                               "Check", 'orion-slot status; echo; orion-slot check && { printf "Install it now? '
                               '[y/N] "; read a; [ "$a" = y ] && '
                               + A.terminal_command(["/usr/local/bin/orion-slot", "update"], pk) + '; }',
                               "suggested-action")]))
        else:
            g.add(self.row("Update OrionOS", "Gets the newest version (asks for the orion-admin password)",
                           icon="system-software-update-symbolic",
                           suffix=[self.terminal_button(
                               "Update", 'echo "Updating OrionOS (needs the orion-admin password)…"; '
                               + A.terminal_command(["/opt/orion/bin/orion-update"], pk), "suggested-action")]))
        ag = self.group("Apps")
        if not d["apps_tool"]:
            self.missing(ag, "App updates (Get apps)", "orion-apps")
        else:
            apps = d["apps"] or []
            if not apps:
                ag.add(self.row("Your apps are up to date", "", icon="emblem-ok-symbolic"))
            for a in apps[:12]:
                a = a if isinstance(a, dict) else {"name": str(a)}
                ver = " → ".join(str(a[k]) for k in ("from", "to") if a.get(k))
                ag.add(self.row(str(a.get("name", "App")), ver, icon="application-x-addon-symbolic"))
            if apps:
                ag.add(self.row(f"{len(apps)} update{'s' if len(apps) != 1 else ''} ready", "",
                                suffix=[self.button("Update all", lambda: (self.win.toast("Updating your apps…"),
                                                                           self.win.act(self.sys.apps_update_all,
                                                                                        after=self.refresh)),
                                                    style="suggested-action")]))
            ag.add(self.row("Get apps", "Find and install more apps", icon="system-software-install-symbolic",
                            suffix=[self.launch_button("Open", ["orion-apps", "open"])]))
        ig = self.group("Install and backup")
        ig.add(self.row("Make my vault (USB stick)", "An encrypted copy of your keys and settings",
                        icon="drive-removable-media-symbolic",
                        suffix=[self.terminal_button("Make", A.terminal_command(["/usr/local/bin/orion-vault", "create"], pk)
                                                     if pk else 'su -P - orion-admin -c "sudo orion-vault create" '
                                                     '2>/dev/null || { echo "Run as admin from the welcome screen: '
                                                     'restart and choose OrionOS."; }')]))
        ig.add(self.row("Install OrionOS to this PC", "Copies the system from the USB stick to this computer's disk",
                        icon="drive-harddisk-symbolic",
                        suffix=[self.terminal_button("Install", A.terminal_command(
                            ["/usr/local/bin/orion-install-disk"], pk))]))
        ig.add(self.row("Bring my stuff from Windows", "Files, bookmarks, Wi-Fi and apps from the OrionMove drive "  # from-windows
                        "that 'Move my stuff to Orion.cmd' made", icon="document-send-symbolic",
                        suffix=[self.terminal_button("Open", "orion-move-in bring")]))
        gg = self.group("Games")  # [games] os/lab/bin/orion-games
        gg.add(self.row("Games", "Find your games (on the Windows drive too) and set up Steam, Heroic (Epic), "
                        "Lutris (Battle.net) or your WoW 1.12 client", icon="applications-games-symbolic",
                        suffix=[self.terminal_button("Open", "orion-games")]))
        gg.add(self.row("GameMode and MangoHud", "Optional: full speed while a game runs, and a frame-rate overlay",
                        icon="utilities-system-monitor-symbolic",
                        suffix=[self.terminal_button("Install", "orion-games setup extras")]))
        # [dualboot] boot counting fell back, and Windows on this computer (os/dualboot)
        bc = d.get("bootcount") or {}
        if bc.get("state") == "fell-back":
            fg = self.group("The last update didn't start")
            fg.add(self.row("OrionOS went back to before the update", str(bc.get("words", "")),
                            icon="dialog-warning-symbolic"))
            fg.add(self.row("Keep the earlier system", "Makes it the real one again (the updated one is kept aside)",
                            suffix=[self.terminal_button("Keep", 'su -P - orion-admin -c "sudo orion-bootcount '
                                                         'keep-earlier"', "suggested-action")]))
            fg.add(self.row("Try the update again", "Two more starts to reach the Orion screen",
                            suffix=[self.terminal_button("Try again", 'su -P - orion-admin -c "sudo orion-bootcount '
                                                         'try-again"')]))
        if d.get("windows"):
            wg = self.group("Windows")
            wg.add(self.row("Restart into Windows", "Starts Windows once; next time OrionOS starts as usual",
                            icon="system-reboot-symbolic",
                            suffix=[self.launch_button("Restart", ["orion-restart-windows"])]))
            wg.add(self.row("Open Windows files", "Read-only. If BitLocker locks the drive, you type its recovery key",
                            icon="drive-harddisk-symbolic",
                            suffix=[self.terminal_button("Open", "orion-windows-files")]))


# ============================================================================================ Appearance
class AppearancePage(Page):
    key, title, icon, section = "appearance", "Appearance", "preferences-desktop-appearance-symbolic", "Orion"
    blurb = "How windows look and how much things move."

    def load(self) -> Any:
        return {**self.sys.appearance(), "pk": A.pkexec_ready(self.sys)}

    def _calm(self, on: bool, pk: bool) -> None:
        if pk:   # H5: the password window, no terminal
            self.win.act(lambda: A.login_mode(self.sys, "reduced-motion", "on" if on else "off"), after=self.refresh)
            return
        self.win.toast("Opened in a terminal window." if self.sys.terminal(
            "orion-ctl login reduced-motion " + ("on" if on else "off")) else "Couldn't open a terminal.")

    def fill(self, d: dict[str, Any]) -> None:
        g = self.group("Windows")
        self.combo(g, "Window style", "Glass is see-through with a soft blur (needs a graphics card). Solid is "
                   "opaque and lighter on old computers.", [("glass", "Orion Glass"), ("solid", "Solid")],
                   "solid" if d["solid"] else "glass", lambda v: self.sys.set_solid(v == "solid"))
        mg = self.group("Motion")
        self.switch(mg, "Reduce motion", "Simple fades instead of windows growing and spiralling. The Orion window "
                    "follows this too.", not d["animations"], lambda on: self.sys.set_animations(not on))
        sw = Adw.SwitchRow(title="Calm login screen and boot splash",
                           subtitle="A simple fade instead of the swirl into the black hole (asks for the admin "
                                    "password" + ("" if d.get("pk") else " in a terminal") + ")", active=d["login_reduced"])
        sw.connect("notify::active", lambda r, _p: self._calm(r.get_active(), bool(d.get("pk"))))
        mg.add(sw)
        sg = self.group("Sky")
        sg.add(self.row("Rare sky events", "Now and then a shooting star, or a comet around the black hole. Set it "
                        "in the Orion window: Settings, then Look & motion.", icon="weather-few-clouds-night-symbolic",
                        suffix=[self.launch_button("Go there", ["orion-ctl", "hud"])]))


# ============================================================================================ Orion
PROVIDERS = [("auto", "Automatic"), ("gemini", "Gemini"), ("anthropic", "Claude"),
             ("openai", "OpenAI")]


class OrionPage(Page):
    key, title, icon, section = "orion", "Orion", "starred-symbolic", "Orion"
    blurb = "Orion's brain, voice, your phone and your projects."

    def load(self) -> Any:
        return self.sys.orion()

    def fill(self, d: dict[str, Any]) -> None:
        g = self.group("Orion's brain")
        g.add(self.row("Status", f"Running, version {d['version']}" if d["running"] and d["version"] else
                       "Running" if d["running"] else "Not answering right now", icon="emblem-system-symbolic",
                       suffix=[self.pill("running" if d["running"] else "stopped", "ok" if d["running"] else "warn"),
                               self.terminal_button("Details", "orion-ctl status")]))
        self.combo(g, "Online brain", "For hard questions. Automatic uses the best one you've set up. Keys are "
                   "added during setup and never shown here.", PROVIDERS, d["provider"] if d["provider"] in dict(PROVIDERS) else "auto",
                   lambda v: self.sys.set_daemon_env("CLOUD_PROVIDER", v))
        models = d["models"] or [d["local_model"]]
        if d["local_model"] not in models:
            models = [d["local_model"], *models]
        self.combo(g, "On-this-computer brain", "Quick answers without the internet (Ollama)" +
                   ("" if d["models"] else ": no other models found"), [(m, m) for m in models], d["local_model"],
                   lambda v: self.sys.set_daemon_env("OLLAMA_MODEL", v), sensitive=bool(d["models"]))
        g.add(self.row("Restart Orion's brain", "If Orion stops answering", icon="view-refresh-symbolic",
                       suffix=[self.button("Restart", lambda: self.win.act(self.sys.restart_brain,
                                                                           after=self.refresh))]))
        vg = self.group("Voice")
        mic = d["mic"] or {}
        if d["mic"] is None:
            vg.add(self.row("Hot mic", "Orion's brain isn't answering, so voice can't be changed right now.",
                            icon="audio-input-microphone-symbolic"))
        else:
            self.switch(vg, "Hot mic", "Orion listens for your voice only (Ctrl+Alt+M)", bool(mic.get("listening")),
                        lambda on: self.sys.hotmic("on" if on else "off"))
            self.switch(vg, "Say “Orion” first", "Orion only answers after it hears its name",
                        bool(mic.get("require_wake", True)), self.sys.wake_word)
        vg.add(self.row("Set up my voice", "Teach Orion your voice and your room (about 2 minutes)",
                        icon="audio-input-microphone-symbolic",
                        suffix=[self.terminal_button("Start", 'orion-ctl mic status | grep -q "speech detector\\|'
                                                     'Whisper" && { echo "Voice is not installed yet. Asking your '
                                                     'phone to approve it:"; orion-ctl setup voice; } || { orion-ctl '
                                                     'voice calibrate && orion-ctl voice enroll; }')]))
        pg = self.group("Phone and approvals")
        pg.add(self.row("Pair my phone", "Shows a code to scan with Orion Mobile", icon="phone-symbolic",
                        suffix=[self.terminal_button("Pair", "orion-ctl pair-phone", "suggested-action")]))
        pg.add(self.row("Paired phones", "", icon="phone-symbolic", suffix=[self.terminal_button("Show", "orion-ctl phones")]))
        pg.add(self.row("Phone alerts", "How loudly your phone tells you about things", icon="preferences-system-notifications-symbolic",
                        suffix=[self.terminal_button("Change", 'orion-ctl alerts; echo; echo "Change one: orion-ctl '
                                                     'alerts openwow digest   (or: lock / unlock)"')]))
        pg.add(self.row("Pair the boardroom", "The signing computer that approves money and code",
                        icon="security-high-symbolic", suffix=[self.terminal_button("Pair", "orion-ctl pair-boardroom")]))
        pg.add(self.row("Make a test decision", "Sends a practice approval to the boardroom",
                        icon="mail-send-symbolic", suffix=[self.button("Send", lambda: self.win.act(self.sys.simulate))]))
        jg = self.group("Your projects")
        e = Adw.EntryRow(title="OpenWoW repo address (https://github.com/…)", show_apply_button=True)
        e.connect("apply", lambda row: self.win.act(lambda: self.sys.set_repo(row.get_text().strip())))
        jg.add(e)
        jg.add(self.row("Set up my workspace", "Checks which apps and code are still missing and asks you to approve "
                        "them", icon="folder-download-symbolic", suffix=[self.terminal_button("Check", "orion-ctl setup")]))
        jg.add(self.row("Orion Files", "Your projects, captures and vault", icon="system-file-manager-symbolic",
                        suffix=[self.launch_button("Open", ["orion-files"])]))


# ============================================================================================ permission levels
LEVEL_NAMES = ["Always ask", "Tell me after", "Just do it"]
# docs/ORIONOS_COMPLETE_PLAN.md Part 3 "Permission levels (default in bold)". `setting` names where the real
# choice is stored; None = not built yet (complete plan step 4), so the row is shown read-only with the default.
PERMISSION_ROWS: list[dict[str, Any]] = [
    {"kind": "Security updates", "default": 3, "note": "With a snapshot first, undone if something breaks",
     "setting": "unattended-upgrades"},
    {"kind": "App and Flatpak updates", "default": 2, "note": "", "setting": None},
    {"kind": "Restarting a stuck service, clearing caches", "default": 3, "note": "", "setting": None},
    {"kind": "New system version", "default": 1, "note": "Into the spare system copy, with automatic rollback",
     "setting": None},
    {"kind": "AI model updates", "default": 2, "note": "", "setting": None},
    {"kind": "Orion changing its own screens", "default": 1, "note": "", "setting": None},
    {"kind": "Orion changing its own brain", "default": 1, "note": "Needs your fingerprint", "setting": None},
]
CORE = ("Protected core", "Approvals, the signer, the firewall, privacy rules and the self-change gate. Always asks, "
        "with your fingerprint. Never automatic. Can't be raised.")


class PermissionsPage(Page):
    key, title, icon, section = "permissions", "Orion's permission levels", "channel-secure-symbolic", "Orion"
    blurb = "How far Orion may go on its own. Raising a level always needs your OK."

    def load(self) -> Any:
        auto = self.sys.r.read(self.sys.root + "/etc/apt/apt.conf.d/20auto-upgrades") or ""
        return {"security_auto": 'Unattended-Upgrade "1"' in auto, "learned": self.sys.autonomy()}

    def fill(self, d: dict[str, Any]) -> None:
        g = self.group("Changes to this computer and to Orion",
                       "These levels arrive with Orion's self-running update. Until then each row works as shown: "
                       "the default, which you'll be able to change here.")
        grid = Gtk.Grid(column_spacing=10, row_spacing=8, margin_top=10, margin_bottom=10, margin_start=14,
                        margin_end=14)
        grid.add_css_class("orion-levels")
        head = Gtk.Label(label="Kind of change", xalign=0, hexpand=True)
        head.add_css_class("head")
        grid.attach(head, 0, 0, 1, 1)
        for i, n in enumerate(LEVEL_NAMES):
            h = Gtk.Label(label=f"{n}")
            h.add_css_class("head")
            h.add_css_class("cell")
            grid.attach(h, i + 1, 0, 1, 1)
        y = 1
        for row in PERMISSION_ROWS:
            level, live = row["default"], False
            if row["setting"] == "unattended-upgrades":
                level, live = (3 if d["security_auto"] else 1), True
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
            k = Gtk.Label(label=row["kind"], xalign=0, wrap=True)
            k.add_css_class("kind")
            box.append(k)
            note = row["note"]
            note = (note + ". " if note else "") + ("This is how it's set now." if live else "Default; not "
                                                                                            "changeable yet.")
            n = Gtk.Label(label=note, xalign=0, wrap=True)
            n.add_css_class("note")
            box.append(n)
            grid.attach(box, 0, y, 1, 1)
            for i in range(3):
                dot = Gtk.Box(halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER)
                dot.add_css_class("orion-level-dot")
                if i + 1 == level:
                    dot.add_css_class("on")
                dot.set_tooltip_text(LEVEL_NAMES[i] + (" (current)" if i + 1 == level else ""))
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
        t = Gtk.Label(label=CORE[0], xalign=0)
        t.add_css_class("kind")
        cb.append(t)
        n = Gtk.Label(label=CORE[1], xalign=0, wrap=True)
        n.add_css_class("note")
        cb.append(n)
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
                        "Orion asks to raise one of these after a run of approvals. Raising always waits for your "
                        "fingerprint on your phone; any edit or “no” drops it back a level.")
        learned = d["learned"]
        if learned is None:
            lg.add(self.row("Orion's brain isn't answering", "Start it to see what it has learned.",
                            icon="dialog-information-symbolic"))
            return
        names = {0: "Always ask", 1: "Suggests, still asks", 2: "Tells you after", 3: "Just does it"}
        for a in learned:
            lvl = int(a.get("level", 0))
            sub = f"{a.get('streak', 0)} approvals in a row"
            if a.get("next_level_at"):
                sub += f"; Orion may ask to raise it at {a['next_level_at']}"
            elif int(a.get("max_level", 0)) <= lvl:
                sub += "; this is as far as it can go"
            lg.add(self.row(str(a.get("label", a.get("task_type", ""))), sub, icon="emblem-default-symbolic",
                            suffix=[self.pill(names.get(lvl, str(lvl)), "ok" if lvl >= 2 else "")]))


from .backups_page import BackupsPage  # noqa: E402  (Toolkit: Backup with restore drills; Try with Craft)
from .everyone_pages import AccessibilityPage, LanguagesPage  # noqa: E402  (step 6: Everyone can use it)

ALL = [NetworkPage, BluetoothPage, SoundPage, DisplaysPage, PrintersPage, PowerPage, TimePage, UsersPage,
       AccessibilityPage, LanguagesPage,
       PrivacyPage, UpdatesPage, BackupsPage, AppearancePage, OrionPage, PermissionsPage]

# Safety net (complete plan step 3): Snapshots & undo next to Updates, Health & kill switch at the end
from .safety_pages import HealthPage, SnapshotsPage  # noqa: E402
ALL.insert(ALL.index(UpdatesPage) + 1, SnapshotsPage)
ALL.append(HealthPage)
# Toolkit tools 7-9 (safety_page.py): Package Reality Check, Injection Firewall, Key Proxy
from .safety_page import AgentSafetyPage  # noqa: E402
ALL.append(AgentSafetyPage)
from .toolkit_pages import RaidNightPage  # noqa: E402  Toolkit: Raid Night Autopilot (toolkit_pages.py)
ALL.append(RaidNightPage)
# Self-running (complete plan step 4): editable permission levels; update channel + VM test switch (self_pages.py)
from .self_pages import SelfPermissionsPage, SelfUpdatesPage  # noqa: E402
ALL[ALL.index(PermissionsPage)] = SelfPermissionsPage
ALL[ALL.index(UpdatesPage)] = SelfUpdatesPage
from .security_page import SecurityPage  # noqa: E402  Toolkit 17/19/20/21: Security check-up (security_page.py)
ALL.append(SecurityPage)
from .tripwire_pages import TripwiresPage  # noqa: E402  Toolkit: Tripwires (tripwire_pages.py)
ALL.append(TripwiresPage)
# H13 + cheap feature 2 (desktop_pages.py): Keyboard & windows after Displays; This PC's hardware after Updates
from .desktop_pages import HardwarePage, KeyboardWindowsPage  # noqa: E402
ALL.insert(ALL.index(DisplaysPage) + 1, KeyboardWindowsPage)
ALL.insert(ALL.index(SelfUpdatesPage) + 1, HardwarePage)
from .rescue_pages import LockRescuePage  # noqa: E402  B6/B7: Lock & rescue (undo the last change, lock times)
ALL.insert(ALL.index(PrivacyPage) + 1, LockRescuePage)
from .sensors_page import SensorsPage  # noqa: E402  [v1gaps] one privacy dashboard (sensors_page.py)
ALL.insert(ALL.index(PrivacyPage) + 1, SensorsPage)
