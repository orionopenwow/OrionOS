"""A stand-in for the real system, for the unit tests and the screenshot demo (`orion-settings-app --demo`).

It answers the same commands System runs with captured sample outputs (os/settings/tests/samples) and keeps a
little state, so switches move in the demo. Nothing here touches the real machine."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Optional, Sequence

from .backend import Result, Runner

SAMPLES = Path(os.environ.get("ORION_SETTINGS_SAMPLES", Path(__file__).resolve().parent.parent / "tests" / "samples"))

TOOLS = {"nmcli", "wpctl", "bluetoothctl", "powerprofilesctl", "swaymsg", "orion-timezone", "orion-nightlight",
         "orion-boot-sound", "orion-ctl", "orion-clipboard", "orion", "gsettings", "wdisplays", "pavucontrol",
         "blueman-manager", "foot", "pkexec", "python3", "orion-killswitch"}


def sample(name: str) -> str:
    try:
        return (SAMPLES / name).read_text(encoding="utf-8")
    except OSError:
        return ""


class FakeRunner(Runner):
    def __init__(self, missing: Sequence[str] = (), extra: Sequence[str] = ()) -> None:
        self.tools = (TOOLS | set(extra)) - set(missing)
        self.calls: list[list[str]] = []
        self.secrets: list[str] = []
        self.spawned: list[list[str]] = []
        self.files: dict[str, str] = {
            "/sys/class/power_supply/BAT0/capacity": "76\n",
            "/sys/class/power_supply/BAT0/status": "Discharging\n",
            "/etc/systemd/logind.conf.d/60-orion-settings.conf": sample("logind-dropin.conf"),
            "/proc/cmdline": "BOOT_IMAGE=/slots/a/vmlinuz orion.slot=a quiet splash\n",
            "/etc/apt/apt.conf.d/20auto-upgrades": 'APT::Periodic::Update-Package-Lists "1";\nAPT::Periodic::Unattended-Upgrade "1";\n',
            "/etc/orion/daemon.env": "OLLAMA_URL=http://127.0.0.1:11434\nCLOUD_PROVIDER=auto\n",
        }
        self.state: dict[str, Any] = {"wifi": True, "bt": True, "night": "on", "boot": True, "profile": "balanced",
                                      "anim": True, "mic": True, "zone": "Europe/Lisbon"}

    # Runner interface ----------------------------------------------------------------------------
    def which(self, name: str) -> bool:
        return name in self.tools

    def spawn(self, argv: Sequence[str]) -> bool:
        self.spawned.append(list(argv))
        return True

    def read(self, path: str) -> Optional[str]:
        if path in self.files:
            return self.files[path]
        for k, v in self.files.items():
            if path.endswith(k) and k.startswith("/sys"):
                return v
        return super().read(path) if "/.config/orion/" in path else None

    def glob(self, pattern: str) -> list[str]:
        return ["/sys/class/power_supply/BAT0"] if "power_supply" in pattern else []

    def http(self, method: str, url: str, body: Optional[dict] = None, timeout: float = 4) -> Optional[Any]:
        if url.endswith("/health"):
            return {"status": "ok", "version": "1.0"}
        if url.endswith("/hotmic"):
            return {"listening": self.state["mic"], "require_wake": True, "profile": True}
        if url.endswith("/api/tags"):
            return {"models": [{"name": "llama3.1:8b"}, {"name": "qwen2.5:14b"}, {"name": "phi3:mini"}]}
        if url.endswith("/boardroom/state"):
            return json.loads(sample("boardroom-state.json") or "{}")
        return None

    def run(self, argv: Sequence[str], timeout: float = 20, secret: Optional[str] = None,
            env: Optional[dict[str, str]] = None) -> Result:
        a = list(argv)
        self.calls.append(a)
        if secret is not None:
            self.secrets.append(secret)
        if a[0] not in self.tools:
            return Result(127, "", f"{a[0]}: not found")
        return self._answer(a)

    # canned answers -------------------------------------------------------------------------------
    def _answer(self, a: list[str]) -> Result:  # noqa: C901 (a lookup table, really)
        s = self.state
        j = " ".join(a)
        if a[0] == "nmcli":
            if a[1:] == ["-t", "-f", "WIFI", "radio"]:
                return Result(0, "enabled\n" if s["wifi"] else "disabled\n")
            if "radio" in a:
                s["wifi"] = a[-1] == "on"
                return Result(0)
            if "device" in a and "DEVICE,TYPE,STATE,CONNECTION" in a:
                return Result(0, sample("nmcli-devices.txt"))
            if "connection" in a and "show" in a:
                return Result(0, sample("nmcli-connections.txt"))
            if "wifi" in a and "list" in a:
                return Result(0, sample("nmcli-wifi-list.txt"))
            return Result(0, "Connection successfully activated.\n")
        if a[0] == "wpctl":
            if a[1] == "status":
                return Result(0, sample("wpctl-status-0.4.txt"))
            if a[1] == "get-volume":
                return Result(0, "Volume: 0.55\n" if "SINK" in a[2] else "Volume: 0.80\n")
            return Result(0)
        if a[0] == "bluetoothctl":
            if a[1] == "show":
                return Result(0, sample("bt-show.txt").replace("Powered: yes", f"Powered: {'yes' if s['bt'] else 'no'}"))
            if a[1] == "devices":
                return Result(0, sample("bt-devices.txt"))
            if a[1] == "info":
                info = sample("bt-info.txt")
                if a[2] != "38:18:4C:12:9A:7E":
                    names = {"F4:7B:09:C1:22:10": ("Desk Keyboard", "input-keyboard", "yes", "no"),
                             "7C:A1:AE:00:11:22": ("Living Room Speaker", "audio-card", "no", "no")}
                    n, icon, paired, conn = names.get(a[2], ("", "", "no", "no"))
                    info = (f"Device {a[2]} (public)\n\tName: {n}\n\tAlias: {n}\n\tIcon: {icon}\n"
                            f"\tPaired: {paired}\n\tConnected: {conn}\n") if n else ""
                return Result(0, info)
            if a[1] == "power":
                s["bt"] = a[2] == "on"
            return Result(0, "Changing succeeded\n")
        if a[0] == "powerprofilesctl":
            if a[1] == "list":
                txt = sample("powerprofiles-list.txt").replace("* balanced", "  balanced")
                txt = txt.replace(f"  {s['profile']}:", f"* {s['profile']}:")
                return Result(0, txt)
            if a[1] == "set":
                s["profile"] = a[2]
            return Result(0)
        if a[0] == "swaymsg":
            return Result(0, sample("sway-outputs.json") if "get_outputs" in a else "[{\"success\": true}]")
        if a[0] == "orion-timezone":
            if a[1] == "status":
                return Result(0, sample("timezone-status.txt").replace("Europe/Lisbon", s["zone"]))
            if a[1] == "set":
                s["zone"] = a[2]
                return Result(0, f"Time zone: {a[2]}. The clock now shows 21:14.\n")
            if a[1] == "lookup":
                return Result(0, "Europe/Lisbon\n")
        if a[0] == "python3" and a[-1] == "zones":
            return Result(0, "Europe/Lisbon  ·  Portugal\nAtlantic/Madeira  ·  Portugal\nUTC  ·  Coordinated Universal "
                             "Time\nAmerica/New_York  ·  United States\nAsia/Tokyo  ·  Japan\nEurope/London  ·  "
                             "United Kingdom\n")
        if a[0] == "orion-nightlight":
            if a[1] == "status":
                return Result(0, f"Night light: {s['night']}\n")
            s["night"] = a[1]
            return Result(0)
        if a[0] == "orion-boot-sound":
            if a[1] == "status":
                return Result(0, "Boot sound: on\n" if s["boot"] else "Boot sound: off\n")
            s["boot"] = a[1] == "on"
            return Result(0, "The boot sound is on.\n" if s["boot"] else "The boot sound is off.\n")
        if a[0] == "orion-ctl":
            if a[1:3] == ["login", "status"]:
                return Result(0, "Login: straight into Orion (auto-login).\nLogin animation: the swirl into the black hole.\n")
            if a[1] == "mic" and a[2] in ("on", "off"):
                s["mic"] = a[2] == "on"
                return Result(0, "Hot mic is ON: Orion answers your voice only. Ctrl+Alt+M mutes it.\n"
                              if s["mic"] else "Hot mic is off.\n")
            return Result(0)
        if a[0] == "gsettings":
            if a[1] == "get":
                return Result(0, "true\n" if s["anim"] else "false\n")
            if "enable-animations" in a:
                s["anim"] = a[-1] == "true"
            return Result(0)
        if a[0] == "pkexec":
            return Result(0)
        if a[0] == "orion-apps":
            if a[1] == "updates":
                return Result(0, json.dumps([{"name": "Firefox", "from": "131.0", "to": "131.0.3"},
                                             {"name": "LocalSend", "from": "1.15", "to": "1.16"}]))
            return Result(0)
        if a[0] == "orion-displays":
            return Result(0, json.dumps({"outputs": [], "profiles": [{"name": "Desk"}, {"name": "Laptop only"}]}))
        return Result(0, "")
