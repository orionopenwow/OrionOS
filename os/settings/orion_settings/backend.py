"""Orion Settings: the system side. Every change goes through an existing OrionOS tool, a standard Linux tool that
the session user may already use (nmcli, wpctl, bluetoothctl, powerprofilesctl, swaymsg, gsettings), or a narrow
root helper in /opt/orion/settings/helpers started with pkexec (polkit decides, never sudo from Python).

The command runner is injectable: tests and the screenshot demo pass a fake one (fake.py), so nothing here needs a
real NetworkManager, PipeWire or Bluetooth daemon to be exercised.

Passwords: Wi-Fi passwords go to nmcli through a private pipe (never on the command line, never logged); account
passwords are never handled here at all (passwd runs in a terminal)."""
from __future__ import annotations

import json
import logging
import os
import pwd
import re
import shlex
import shutil
import subprocess
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional, Sequence

from . import parsers as P

log = logging.getLogger("orion.settings")

HELPERS = Path(os.environ.get("ORION_SETTINGS_HELPERS", "/opt/orion/settings/helpers"))
BASICS_LIB = os.environ.get("ORION_BASICS_LIB", "/opt/orion/basics/orion_basics.py")
API = os.environ.get("ORION_API", "http://127.0.0.1:9120")
LOGIND_DROPIN = "/etc/systemd/logind.conf.d/60-orion-settings.conf"
CAMERA_STATE = "/etc/orion/camera"
SSID_OK = re.compile(r"^[^\x00-\x1f]{1,32}$")
ZONE_OK = re.compile(r"^[A-Za-z0-9_+\-]+(/[A-Za-z0-9_+\-]+)*$")
REPO_OK = re.compile(r"^https://[A-Za-z0-9._~:/@-]+$")


@dataclass
class Result:
    rc: int
    out: str = ""
    err: str = ""

    @property
    def ok(self) -> bool:
        return self.rc == 0


class Runner:
    """Runs commands for real. Swap it for fake.FakeRunner in tests and the demo."""

    def run(self, argv: Sequence[str], timeout: float = 20, secret: Optional[str] = None,
            env: Optional[dict[str, str]] = None) -> Result:
        """Run argv. `secret` is handed over through a pipe whose path replaces "{SECRET_FD}" in argv; it never
        appears in argv, the environment or the log."""
        argv = list(argv)
        r_fd = w_fd = None
        try:
            if secret is not None:
                r_fd, w_fd = os.pipe()
                argv = [a.replace("{SECRET_FD}", f"/dev/fd/{r_fd}") for a in argv]
            log.debug("run %s", argv[0])
            p = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                 text=True, pass_fds=(r_fd,) if r_fd is not None else (),
                                 env={**os.environ, **(env or {})})
            if w_fd is not None:
                os.write(w_fd, secret.encode())  # type: ignore[union-attr]
                os.close(w_fd)
                w_fd = None
            out, err = p.communicate(timeout=timeout)
            return Result(p.returncode, out, err)
        except FileNotFoundError:
            return Result(127, "", f"{argv[0]} is not installed")
        except subprocess.TimeoutExpired:
            p.kill()
            return Result(124, "", "it took too long to answer")
        finally:
            for fd in (r_fd, w_fd):
                if fd is not None:
                    try:
                        os.close(fd)
                    except OSError:
                        pass

    def run_input(self, argv: Sequence[str], text: str, timeout: float = 120) -> Result:
        """Run argv with `text` on its stdin (pkexec passes stdin through; it closes every other pipe). Used for
        the account password the admin helper sets (admin.py); never logged, never on the command line."""
        try:
            p = subprocess.run(list(argv), input=text, capture_output=True, text=True, timeout=timeout)
            return Result(p.returncode, p.stdout, p.stderr)
        except FileNotFoundError:
            return Result(127, "", f"{argv[0]} is not installed")
        except subprocess.TimeoutExpired:
            return Result(124, "", "it took too long to answer")

    def which(self, name: str) -> bool:
        return shutil.which(name) is not None

    def spawn(self, argv: Sequence[str]) -> bool:
        """Start a window or terminal and don't wait for it."""
        try:
            subprocess.Popen(list(argv), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, start_new_session=True)
            return True
        except OSError:
            return False

    def read(self, path: str) -> Optional[str]:
        try:
            return Path(path).read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None

    def glob(self, pattern: str) -> list[str]:
        import glob
        return sorted(glob.glob(pattern))

    def http(self, method: str, url: str, body: Optional[dict] = None, timeout: float = 4) -> Optional[Any]:
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method,
                                     headers=daemon_headers({"content-type": "application/json"} if data else {}))
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 (local daemon only)
                return json.loads(r.read().decode() or "null")
        except (urllib.error.URLError, OSError, ValueError):
            return None


def daemon_token() -> str:
    """Orion's per-start API key (review B1; written 0600 by the daemon, apps/daemon/api_token.py). Root helpers
    (apt hooks, timers) look in the session user's runtime folder too. '' when Orion isn't running."""
    import pwd as _pwd
    cands = [os.environ.get("ORION_TOKEN_FILE", "")]
    uid = os.getuid() if hasattr(os, "getuid") else -1
    cands.append(os.path.join(os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{uid}", "orion", "daemon.token"))
    homes = [os.path.expanduser(os.environ.get("ORION_DATA_DIR", "~/.orion"))]
    if uid == 0:
        try:
            pw = _pwd.getpwnam(os.environ.get("ORION_USER", "orion"))
            cands.append(f"/run/user/{pw.pw_uid}/orion/daemon.token")
            homes.append(os.path.join(pw.pw_dir, ".orion"))
        except KeyError:
            pass
    cands += [os.path.join(h, "daemon.token") for h in homes]
    for c in cands:
        try:
            with open(c, encoding="utf-8") as f:
                tok = f.read().strip()
        except OSError:
            continue
        if tok:
            return tok
    return ""


def daemon_headers(extra: Optional[dict] = None) -> dict:
    tok = daemon_token()
    return {**({"Authorization": f"Bearer {tok}"} if tok else {}), **(extra or {})}


def plain_error(res: Result, what: str) -> str:
    """A failed command in plain words (no stack traces, no raw tool jargon where we can avoid it)."""
    msg = (res.err or res.out or "").strip().splitlines()
    detail = msg[-1] if msg else ""
    detail = re.sub(r"^(Error|error|Failed|failed):\s*", "", detail)
    if res.rc == 127:
        return f"Couldn't {what}: the tool for it isn't installed."
    if res.rc == 124:
        return f"Couldn't {what}: it took too long to answer."
    if res.rc in (126, 127) or "Not authorized" in detail or "not authorized" in detail:
        return f"Couldn't {what}: permission was refused."
    return f"Couldn't {what}." + (f" ({detail[:160]})" if detail else "")


class System:
    """Everything the pages read and change. All methods block; the window calls them off the UI thread."""

    def __init__(self, runner: Optional[Runner] = None, home: Optional[str] = None, sysroot: str = "") -> None:
        self.r = runner or Runner()
        self.home = Path(home or os.path.expanduser("~"))
        self.root = sysroot           # prefix for /sys and /etc reads (tests)
        self.conf = self.home / ".config" / "orion"

    # ------------------------------------------------------------------ helpers
    def has(self, tool: str) -> bool:
        return self.r.which(tool)

    def terminal(self, command: str, hold: bool = True) -> bool:
        """Run a shell command in a themed terminal (the same 'orion-tool' window the text menu uses)."""
        if hold:
            command = f'{command}; printf "\\n\\033[2mPress Enter to close\\033[0m"; read _'
        return self.r.spawn(["foot", "--title=orion-tool", "-e", "sh", "-c", command])

    def launch(self, argv: Sequence[str]) -> bool:
        return self.r.spawn(argv)

    def _conf_read(self, name: str) -> Optional[str]:
        t = self.r.read(str(self.conf / name))
        return t.strip() if t is not None else None

    def _conf_write(self, name: str, text: str) -> None:
        self.conf.mkdir(parents=True, exist_ok=True)
        p = self.conf / name
        tmp = p.with_suffix(p.suffix + ".new")
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, p)

    def prefs(self) -> dict[str, Any]:
        try:
            return json.loads(self._conf_read("settings.json") or "{}")
        except ValueError:
            return {}

    def set_pref(self, key: str, value: Any) -> None:
        p = self.prefs()
        p[key] = value
        self._conf_write("settings.json", json.dumps(p, indent=1) + "\n")

    def helper(self, name: str, *args: str) -> Result:
        """A narrow root helper through pkexec; polkit (ai.orion.settings.policy + 50-orion-settings.rules)
        decides whether it may run."""
        path = HELPERS / name
        return self.r.run(["pkexec", str(path), *args], timeout=120)

    # ------------------------------------------------------------------ Wi-Fi & network
    def network(self, rescan: bool = False) -> dict[str, Any]:
        if not self.has("nmcli"):
            return {"available": False}
        radio = self.r.run(["nmcli", "-t", "-f", "WIFI", "radio"])
        if not radio.ok:
            return {"available": False, "error": "The network service isn't running."}
        wifi_on = radio.out.strip() == "enabled"
        devices = P.parse_nm_devices(self.r.run(["nmcli", "-t", "-f", "DEVICE,TYPE,STATE,CONNECTION", "device"]).out)
        saved = [c["name"] for c in P.parse_nm_connections(
            self.r.run(["nmcli", "-t", "-f", "NAME,TYPE,DEVICE", "connection", "show"]).out)
            if c["type"] == "802-11-wireless"]
        nets = []
        if wifi_on and any(d["type"] == "wifi" for d in devices):
            nets = P.parse_nm_wifi_list(self.r.run(
                ["nmcli", "-t", "-f", "IN-USE,SSID,SIGNAL,SECURITY", "device", "wifi", "list",
                 "--rescan", "yes" if rescan else "auto"], timeout=30).out)
            for n in nets:
                n["saved"] = n["ssid"] in saved
        return {"available": True, "wifi_on": wifi_on, "devices": devices, "networks": nets,
                "has_wifi": any(d["type"] == "wifi" for d in devices), "saved": saved,
                "airplane": self.airplane_state(wifi_on)}

    def airplane_state(self, wifi_on: bool) -> bool:
        bt = self.bluetooth_state()
        return (not wifi_on) and not (bt and bt.get("powered"))

    def set_wifi(self, on: bool) -> tuple[bool, str]:
        r = self.r.run(["nmcli", "radio", "wifi", "on" if on else "off"])
        return (True, "Wi-Fi is on." if on else "Wi-Fi is off.") if r.ok else (False, plain_error(r, "switch Wi-Fi"))

    def set_airplane(self, on: bool) -> tuple[bool, str]:
        r = self.r.run(["nmcli", "radio", "all", "off" if on else "on"])
        if not r.ok:
            return False, plain_error(r, "change airplane mode")
        if self.has("bluetoothctl"):
            self.r.run(["bluetoothctl", "power", "off" if on else "on"])
        return True, "Airplane mode is on: Wi-Fi and Bluetooth are off." if on else "Airplane mode is off."

    def wifi_connect(self, ssid: str, password: Optional[str] = None, security: str = "") -> tuple[bool, str]:
        if not SSID_OK.match(ssid):
            return False, "That network name can't be used."
        if "802.1X" in security:
            return False, "This network needs a work or school sign-in. Use 'More network options' for it."
        saved = [c["name"] for c in P.parse_nm_connections(
            self.r.run(["nmcli", "-t", "-f", "NAME,TYPE,DEVICE", "connection", "show"]).out)]
        if ssid in saved and password is None:
            r = self.r.run(["nmcli", "--wait", "30", "connection", "up", "id", ssid], timeout=40)
            return (True, f"Connected to {ssid}.") if r.ok else (False, plain_error(r, f"connect to {ssid}"))
        if not security:
            r = self.r.run(["nmcli", "--wait", "30", "device", "wifi", "connect", ssid], timeout=40)
            return (True, f"Connected to {ssid}.") if r.ok else (False, plain_error(r, f"connect to {ssid}"))
        if not password:
            return False, "This network needs its password."
        if len(password) < 8 or (len(password) > 63 and not re.fullmatch(r"[0-9A-Fa-f]{64}", password)):
            return False, "Wi-Fi passwords are 8 to 63 characters long."
        # A saved profile without the password, then "up" with the password through a private pipe: the password
        # never shows in the process list or a log. NetworkManager stores it (root-only file) once it works.
        if ssid in saved:
            self.r.run(["nmcli", "connection", "delete", "id", ssid])
        mgmt = "sae" if "WPA3" in security and "WPA2" not in security else "wpa-psk"
        add = self.r.run(["nmcli", "connection", "add", "type", "wifi", "con-name", ssid, "ssid", ssid,
                          "wifi-sec.key-mgmt", mgmt])
        if not add.ok:
            return False, plain_error(add, f"set up {ssid}")
        up = self.r.run(["nmcli", "--wait", "40", "connection", "up", "id", ssid, "passwd-file", "{SECRET_FD}"],
                        timeout=50, secret=f"802-11-wireless-security.psk:{password}\n")
        if up.ok:
            return True, f"Connected to {ssid}."
        self.r.run(["nmcli", "connection", "delete", "id", ssid])
        if re.search(r"secrets were required|password|802-1x|psk", (up.err + up.out), re.I):
            return False, f"The password for {ssid} didn't work. Check it and try again."
        return False, plain_error(up, f"connect to {ssid}")

    def wifi_forget(self, ssid: str) -> tuple[bool, str]:
        r = self.r.run(["nmcli", "connection", "delete", "id", ssid])
        return (True, f"Forgot {ssid}.") if r.ok else (False, plain_error(r, f"forget {ssid}"))

    def wifi_disconnect(self, device: str) -> tuple[bool, str]:
        r = self.r.run(["nmcli", "device", "disconnect", device])
        return (True, "Disconnected.") if r.ok else (False, plain_error(r, "disconnect"))

    # ------------------------------------------------------------------ Bluetooth
    def bluetooth_state(self) -> Optional[dict[str, Any]]:
        if not self.has("bluetoothctl"):
            return None
        return P.parse_bt_show(self.r.run(["bluetoothctl", "show"], timeout=5).out)

    def bluetooth(self, scan: bool = False) -> dict[str, Any]:
        st = self.bluetooth_state()
        if st is None:
            return {"available": False, "blueman": self.has("blueman-manager"),
                    "tool": self.has("bluetoothctl")}
        if scan and st["powered"]:
            self.r.run(["bluetoothctl", "--timeout", "8", "scan", "on"], timeout=15)
        devs = []
        for d in P.parse_bt_devices(self.r.run(["bluetoothctl", "devices"]).out)[:24]:
            info = P.parse_bt_info(self.r.run(["bluetoothctl", "info", d["address"]], timeout=5).out)
            name = info["name"] or d["name"]
            devs.append({**d, **info, "name": name, "kind": P.bt_kind(info["icon"])})
        devs.sort(key=lambda d: (not d["connected"], not d["paired"], d["name"].lower()))
        return {"available": True, **st, "devices": devs, "blueman": self.has("blueman-manager")}

    def bt_power(self, on: bool) -> tuple[bool, str]:
        r = self.r.run(["bluetoothctl", "power", "on" if on else "off"])
        if r.ok and "Failed" not in r.out:
            return True, "Bluetooth is on." if on else "Bluetooth is off."
        return False, plain_error(r, "switch Bluetooth") + (" Is airplane mode on?" if on else "")

    def bt_action(self, action: str, mac: str) -> tuple[bool, str]:
        if not P.MAC_RE.match(mac) or action not in ("pair", "connect", "disconnect", "remove"):
            return False, "That device can't be used."
        words = {"pair": "pair with it", "connect": "connect to it", "disconnect": "disconnect it",
                 "remove": "forget it"}[action]
        if action == "pair":
            r = self.r.run(["bluetoothctl", "--timeout", "25", "pair", mac], timeout=35)
            if not r.ok or "Failed" in r.out:
                return False, ("Couldn't pair. If the device shows a code, use 'More Bluetooth options' to pair "
                               "it there.")
            self.r.run(["bluetoothctl", "trust", mac])
            r = self.r.run(["bluetoothctl", "--timeout", "15", "connect", mac], timeout=25)
            return True, "Paired." + (" Connected." if r.ok and "Failed" not in r.out else "")
        r = self.r.run(["bluetoothctl", "--timeout", "15", action, mac], timeout=25)
        if r.ok and "Failed" not in r.out:
            return True, {"connect": "Connected.", "disconnect": "Disconnected.", "remove": "Forgotten."}[action]
        return False, plain_error(r, words)

    # ------------------------------------------------------------------ Sound
    def sound(self) -> dict[str, Any]:
        if not self.has("wpctl"):
            return {"available": False, "boot_sound": self.boot_sound()}
        st = self.r.run(["wpctl", "status"], timeout=5)
        if not st.ok:
            return {"available": False, "error": "The sound service isn't running.", "boot_sound": self.boot_sound()}
        nodes = P.parse_wpctl_status(st.out)
        vol, muted = P.parse_wpctl_volume(self.r.run(["wpctl", "get-volume", "@DEFAULT_AUDIO_SINK@"]).out)
        mic, mic_muted = P.parse_wpctl_volume(self.r.run(["wpctl", "get-volume", "@DEFAULT_AUDIO_SOURCE@"]).out)
        return {"available": True, **nodes, "volume": vol, "muted": muted, "mic_volume": mic,
                "mic_muted": mic_muted, "boot_sound": self.boot_sound(), "mixer": self.has("pavucontrol")}

    def set_volume(self, which: str, frac: float) -> tuple[bool, str]:
        target = "@DEFAULT_AUDIO_SOURCE@" if which == "input" else "@DEFAULT_AUDIO_SINK@"
        frac = max(0.0, min(1.5, frac))
        r = self.r.run(["wpctl", "set-volume", "-l", "1.5", target, f"{frac:.2f}"])
        return (True, "") if r.ok else (False, plain_error(r, "change the volume"))

    def set_mute(self, which: str, muted: bool) -> tuple[bool, str]:
        target = "@DEFAULT_AUDIO_SOURCE@" if which == "input" else "@DEFAULT_AUDIO_SINK@"
        r = self.r.run(["wpctl", "set-mute", target, "1" if muted else "0"])
        return (True, "") if r.ok else (False, plain_error(r, "mute"))

    def set_default_audio(self, node_id: int) -> tuple[bool, str]:
        r = self.r.run(["wpctl", "set-default", str(int(node_id))])
        return (True, "Switched.") if r.ok else (False, plain_error(r, "switch the sound device"))

    def boot_sound(self) -> Optional[bool]:
        if not self.has("orion-boot-sound"):
            return None
        return "on" in self.r.run(["orion-boot-sound", "status"]).out

    def set_boot_sound(self, on: bool) -> tuple[bool, str]:
        r = self.r.run(["orion-boot-sound", "on" if on else "off"])
        return (True, r.out.strip()) if r.ok else (False, plain_error(r, "change the boot sound"))

    # ------------------------------------------------------------------ Displays
    def displays(self) -> dict[str, Any]:
        out: dict[str, Any] = {"orion_displays": self.has("orion-displays"), "wdisplays": self.has("wdisplays"),
                               "nightlight": self.nightlight(), "outputs": [], "profiles": []}
        if out["orion_displays"]:
            data = P.parse_json_maybe(self.r.run(["orion-displays", "list", "--json"], timeout=8).out)
            if isinstance(data, dict):
                out["profiles"] = [p if isinstance(p, str) else str(p.get("name", ""))
                                   for p in data.get("profiles", []) if p]
        if self.has("swaymsg"):
            out["outputs"] = P.parse_sway_outputs(self.r.run(["swaymsg", "-t", "get_outputs", "-r"]).out)
        return out

    def nightlight(self) -> Optional[str]:
        if not self.has("orion-nightlight"):
            return None
        return P.parse_nightlight(self.r.run(["orion-nightlight", "status"]).out)

    def set_nightlight(self, on: bool) -> tuple[bool, str]:
        r = self.r.run(["orion-nightlight", "on" if on else "off"], env={"QUIET": "1"}, timeout=10)
        st = self.nightlight()
        if on and st == "unavailable":
            return False, "This screen can't change its colour (common in virtual machines)."
        return (True, "Night light is on." if on else "Night light is off.") if r.ok else \
            (False, plain_error(r, "switch the night light"))

    def set_scale(self, output: str, scale: float) -> tuple[bool, str]:
        if not re.fullmatch(r"[A-Za-z0-9_.-]{1,40}", output) or not 0.5 <= scale <= 3:
            return False, "That size can't be used."
        r = self.r.run(["swaymsg", f"output {output} scale {scale:g}"])
        if not r.ok:
            return False, plain_error(r, "change the size")
        # Kept for the next start: Sway reads ~/.config/orion/sway.d/*.conf (the os-settings block in sway-config).
        d = self.conf / "sway.d"
        d.mkdir(parents=True, exist_ok=True)
        f = d / "displays.conf"
        lines = [ln for ln in (self.r.read(str(f)) or "").splitlines() if not ln.startswith(f"output {output} scale")]
        lines.append(f"output {output} scale {scale:g}")
        f.write_text("# Orion Settings > Displays (text size)\n" +
                     "\n".join(ln for ln in lines if not ln.startswith("#")) + "\n", encoding="utf-8")
        return True, f"Size set to {int(scale * 100)}%."

    # ------------------------------------------------------------------ Power
    def power(self) -> dict[str, Any]:
        out: dict[str, Any] = {"profiles": [], "active": None, "batteries": [], "lid": {}}
        if self.has("powerprofilesctl"):
            r = self.r.run(["powerprofilesctl", "list"], timeout=5)
            if r.ok:
                out["profiles"], out["active"] = P.parse_power_profiles(r.out)
        for bat in self.r.glob(f"{self.root}/sys/class/power_supply/BAT*"):
            cap = (self.r.read(f"{bat}/capacity") or "").strip()
            status = (self.r.read(f"{bat}/status") or "").strip()
            if cap.isdigit():
                out["batteries"].append({"name": Path(bat).name, "percent": int(cap), "status": status})
        out["lid"] = P.parse_logind_dropin(self.r.read(self.root + LOGIND_DROPIN) or "")
        return out

    def set_profile(self, name: str) -> tuple[bool, str]:
        if name not in P.PROFILE_WORDS:
            return False, "Unknown power mode."
        r = self.r.run(["powerprofilesctl", "set", name])
        return (True, f"{P.PROFILE_WORDS[name][0]} is on.") if r.ok else (False, plain_error(r, "change the power mode"))

    def set_logind(self, key: str, value: str) -> tuple[bool, str]:
        arg = {"HandleLidSwitch": "lid", "HandleLidSwitchExternalPower": "lid-plugged-in",
               "HandlePowerKey": "power-button"}.get(key)
        if arg is None or value not in ("suspend", "lock", "ignore", "poweroff", "hibernate", "suspend-then-hibernate"):
            return False, "That choice can't be used."
        r = self.helper("orion-settings-power", arg, value)
        return (True, "Saved.") if r.ok else (False, plain_error(r, "save the power setting"))

    # ------------------------------------------------------------------ Time
    def time(self) -> dict[str, Any]:
        if not self.has("orion-timezone"):
            return {"available": False}
        st = P.parse_timezone_status(self.r.run(["orion-timezone", "status"], timeout=5).out)
        return {"available": True, **st, "lookup_allowed": self.prefs().get("timezone_lookup", True)}

    def zones(self) -> list[tuple[str, str]]:
        return P.parse_zone_lines(self.r.run(["python3", BASICS_LIB, "zones"], timeout=10).out)

    def set_zone(self, zone: str) -> tuple[bool, str]:
        if not ZONE_OK.match(zone):
            return False, "That isn't a time zone."
        r = self.r.run(["orion-timezone", "set", zone], timeout=20)
        return (True, r.out.strip() or f"Time zone: {zone}.") if r.ok else (False, plain_error(r, "change the time zone"))

    def lookup_zone(self) -> Optional[str]:
        r = self.r.run(["orion-timezone", "lookup"], timeout=12)
        z = r.out.strip()
        return z if r.ok and ZONE_OK.match(z or "-") else None

    # ------------------------------------------------------------------ Users
    def user(self) -> dict[str, Any]:
        pw = pwd.getpwuid(os.getuid())
        name = ""
        for line in (self.r.read(str(self.conf / "profile.conf")) or "").splitlines():
            if line.startswith("NAME="):
                name = line[5:].strip()
        login = P.parse_login_status(self.r.run(["orion-ctl", "login", "status"], timeout=5).out) \
            if self.has("orion-ctl") else {"password": False, "reduced_motion": False}
        return {"login": pw.pw_name, "full": (pw.pw_gecos or "").split(",")[0], "greeting_name": name,
                "password_login": login["password"]}

    def set_greeting_name(self, name: str) -> tuple[bool, str]:
        name = re.sub(r"[\r\n]", "", name).strip()[:40]
        r = self.r.run(["orion", "name", *([name] if name else [])])
        return (True, r.out.strip()) if r.ok else (False, plain_error(r, "save the name"))

    # ------------------------------------------------------------------ Privacy
    def privacy(self) -> dict[str, Any]:
        cam = (self.r.read(self.root + CAMERA_STATE) or "").strip() or "off"
        env = P.parse_env_file(self.r.read(str(self.conf / "daemon.env")) or "")
        # Screen reading is opt-in (B7): the user's own file wins, then the system default (off)
        sys_env = P.parse_env_file(self.r.read(self.root + "/etc/orion/daemon.env") or "")
        cap = env.get("ORION_CAPTURE_ENABLED", sys_env.get("ORION_CAPTURE_ENABLED", "0")).lower() in (
            "1", "true", "yes", "on")
        clip = (self._conf_read("clipboard-history") or "on") != "off"
        return {"camera": cam == "on", "camera_helper": True, "capture": cap, "clipboard": clip,
                "timezone_lookup": self.prefs().get("timezone_lookup", True),
                "killswitch": self.has("orion-killswitch")}

    def set_camera(self, on: bool) -> tuple[bool, str]:
        r = self.helper("orion-settings-camera", "on" if on else "off")
        if r.ok:
            return True, "The camera is on." if on else "The camera is off. Apps can't see it."
        return False, plain_error(r, "switch the camera")

    def set_daemon_env(self, key: str, value: Optional[str], restart: bool = True) -> tuple[bool, str]:
        """Change one setting in the user's own ~/.config/orion/daemon.env (other lines untouched; only the named
        key is read or written, so any keys kept there are never shown here), then restart Orion's brain."""
        path = self.conf / "daemon.env"
        text = self.r.read(str(path)) or ""
        new = P.set_env_line(text, key, value)
        self.conf.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name("daemon.env.new")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(new)
        os.replace(tmp, path)
        if restart and self.has("orion-ctl"):
            self.r.run(["orion-ctl", "restart"], timeout=20)
        return True, "Saved. Orion's brain restarted to use it." if restart else "Saved."

    def set_capture(self, on: bool) -> tuple[bool, str]:
        return self.set_daemon_env("ORION_CAPTURE_ENABLED", "1" if on else "0")

    def set_clipboard(self, on: bool) -> tuple[bool, str]:
        self._conf_write("clipboard-history", "on\n" if on else "off\n")
        return True, "Clipboard history is on." if on else "Clipboard history is off. New copies aren't kept."

    def clear_clipboard(self) -> tuple[bool, str]:
        r = self.r.run(["orion-clipboard", "clear"])
        return (True, "Clipboard history cleared.") if r.ok else (False, plain_error(r, "clear the history"))

    # ------------------------------------------------------------------ Updates
    def updates(self) -> dict[str, Any]:
        slot = "orion.slot" in (self.r.read(self.root + "/proc/cmdline") or "")
        out: dict[str, Any] = {"slot": slot, "apps_tool": self.has("orion-apps"), "apps": None,
                               "share": self.has("orion-share")}
        if out["apps_tool"]:
            data = P.parse_json_maybe(self.r.run(["orion-apps", "updates", "--json"], timeout=60).out)
            if isinstance(data, dict):
                data = data.get("updates", data.get("apps", []))
            out["apps"] = data if isinstance(data, list) else []
        # [dualboot] Restart into Windows / Open Windows files, and boot counting after updates (os/dualboot)
        out["windows"] = self.has("orion-restart-windows") and \
            self.r.run(["orion-restart-windows", "status"], timeout=10).ok
        bc = P.parse_json_maybe(self.r.run(["orion-bootcount", "status", "--json"], timeout=10).out) \
            if self.has("orion-bootcount") else None
        out["bootcount"] = bc if isinstance(bc, dict) else None
        return out

    def apps_update_all(self) -> tuple[bool, str]:
        r = self.r.run(["orion-apps", "update-all"], timeout=1800)
        return (True, "Apps are up to date.") if r.ok else (False, plain_error(r, "update the apps"))

    # ------------------------------------------------------------------ Appearance
    def appearance(self) -> dict[str, Any]:
        env = P.parse_env_file(self.r.read(str(self.conf / "session.env")) or "")
        anim = self.r.run(["gsettings", "get", "org.gnome.desktop.interface", "enable-animations"]).out.strip()
        login = P.parse_login_status(self.r.run(["orion-ctl", "login", "status"], timeout=5).out) \
            if self.has("orion-ctl") else {"reduced_motion": False}
        return {"solid": env.get("ORION_SWAYFX") == "0", "animations": anim != "false",
                "login_reduced": login["reduced_motion"]}

    def set_solid(self, solid: bool) -> tuple[bool, str]:
        path = self.conf / "session.env"
        text = self.r.read(str(path)) or ""
        self.conf.mkdir(parents=True, exist_ok=True)
        path.write_text(P.set_env_line(text, "ORION_SWAYFX", "0" if solid else None), encoding="utf-8")
        self.r.run(["gsettings", "set", "org.gnome.desktop.interface", "gtk-theme",
                    "Orion-Glass-Solid" if solid else "Orion-Glass"])
        return True, ("Solid windows from now on." if solid else
                      "Glass windows. The see-through blur comes back the next time you log in (it needs a "
                      "graphics card).")

    def set_animations(self, on: bool) -> tuple[bool, str]:
        r = self.r.run(["gsettings", "set", "org.gnome.desktop.interface", "enable-animations",
                        "true" if on else "false"])
        return (True, "") if r.ok else (False, plain_error(r, "change the motion setting"))

    # ------------------------------------------------------------------ Orion
    def orion(self) -> dict[str, Any]:
        sys_env = P.parse_env_file(self.r.read(self.root + "/etc/orion/daemon.env") or "")
        usr_env = P.parse_env_file(self.r.read(str(self.conf / "daemon.env")) or "")
        pick = lambda k, d: usr_env.get(k, sys_env.get(k, d))  # noqa: E731
        health = self.r.http("GET", f"{API}/health")
        mic = self.r.http("GET", f"{API}/hotmic")
        ollama = pick("OLLAMA_URL", "http://localhost:11434").rstrip("/")
        tags = self.r.http("GET", f"{ollama}/api/tags", timeout=2) if ollama.startswith("http") else None
        models = sorted({m.get("name", "") for m in (tags or {}).get("models", []) if m.get("name")})
        return {"running": bool(health), "version": (health or {}).get("version"),
                "provider": pick("CLOUD_PROVIDER", "auto").lower(),
                "local_model": pick("OLLAMA_MODEL", "llama3.1:8b"), "models": models,
                "mic": mic, "personality": pick("ORION_PERSONALITY", "concise_companion")}

    def hotmic(self, action: str) -> tuple[bool, str]:
        if action not in ("on", "off"):
            return False, ""
        r = self.r.run(["orion-ctl", "mic", action], timeout=10)
        return (True, r.out.strip()) if r.ok else (False, plain_error(r, "switch the hot mic"))

    def wake_word(self, required: bool) -> tuple[bool, str]:
        r = self.r.run(["orion-ctl", "mic", "wake-word", "true" if required else "false"], timeout=10)
        return (True, r.out.strip()) if r.ok else (False, plain_error(r, "change the wake word"))

    def set_repo(self, url: str) -> tuple[bool, str]:
        if not REPO_OK.match(url):
            return False, "That doesn't look like a repo address (https://github.com/...)."
        return self.terminal(f"orion-ctl set-repo {shlex.quote(url)}"), "Opened."

    def simulate(self) -> tuple[bool, str]:
        r = self.r.run(["orion-ctl", "simulate", "bounty"], timeout=10)
        return (True, "A test decision is waiting on the boardroom.") if r.ok else \
            (False, plain_error(r, "make a test decision"))

    def restart_brain(self) -> tuple[bool, str]:
        r = self.r.run(["orion-ctl", "restart"], timeout=20)
        return (True, "Brain restarted.") if r.ok else (False, plain_error(r, "restart Orion's brain"))

    # ------------------------------------------------------------------ permission levels
    def autonomy(self) -> Optional[list[dict[str, Any]]]:
        st = self.r.http("GET", f"{API}/boardroom/state")
        return st.get("autonomy") if isinstance(st, dict) else None
