"""The outside world for the self-running parts (self_repair, self_updates, update_channels, weekly_review).

Everything they do to the computer goes through one Host object: run a program, find one, open a terminal, talk
to the local Ollama. Tests swap in a fake Host (fake systemctl, ollama, fwupdmgr, QEMU runner...), so nothing
real is touched.

Root actions only ever go through the fixed helper (os/safety/orion-self-helper, started with pkexec; polkit lets
the session user restart allow-listed services and trim the journal without a password, nothing else).
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
import urllib.request
from typing import Any, Optional

HELPER = os.environ.get("ORION_SELF_HELPER", "/opt/orion/safety/orion-self-helper")

# System services Orion may restart by itself (the helper checks the same list again, as root).
SYSTEM_UNITS = ("NetworkManager.service", "bluetooth.service", "ollama.service", "cups.service",
                "systemd-resolved.service", "systemd-timesyncd.service", "avahi-daemon.service", "fwupd.service")
# Your own (user) services Orion may restart: by name prefix.
USER_UNIT_PREFIXES = ("orion-", "pipewire", "wireplumber", "mako", "kanshi", "xdg-desktop-portal", "gammastep",
                      "wlsunset", "cliphist", "ollama")


def system_unit_allowed(unit: str) -> bool:
    return unit in SYSTEM_UNITS or (unit.startswith("orion-") and unit.endswith(".service")
                                    and "/" not in unit and " " not in unit)


def user_unit_allowed(unit: str) -> bool:
    return unit.endswith((".service", ".socket")) and "/" not in unit and " " not in unit \
        and unit.startswith(USER_UNIT_PREFIXES)


class Host:
    def run(self, argv: list[str], timeout: float = 120) -> tuple[int, str]:
        """(exit code, output + errors). 127 when the program isn't there, 124 on timeout."""
        try:
            r = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
            return r.returncode, ((r.stdout or "") + (r.stderr or ""))[-20000:]
        except FileNotFoundError:
            return 127, f"{argv[0]} isn't installed"
        except subprocess.TimeoutExpired:
            return 124, "it took too long"
        except OSError as exc:
            return 126, str(exc)

    def which(self, name: str) -> Optional[str]:
        return shutil.which(name)

    def exists(self, path: str) -> bool:
        return os.path.exists(path)

    def helper(self, *args: str, timeout: float = 120) -> tuple[int, str]:
        if not self.exists(HELPER):
            return 127, "the OrionOS helper isn't installed on this computer"
        if os.name != "nt" and os.geteuid() == 0:
            return self.run([HELPER, *args], timeout)
        if not self.which("pkexec"):
            return 127, "pkexec isn't installed"
        return self.run(["pkexec", HELPER, *args], timeout)

    def open_terminal(self, command: str) -> bool:
        try:
            import snapshots
            return snapshots._open_terminal(command)
        except Exception:
            return False

    def http_json(self, method: str, url: str, body: Optional[dict] = None, timeout: float = 60) -> Optional[Any]:
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method,
                                     headers={"content-type": "application/json"} if data else {})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 (local Ollama / daemon only)
                return json.loads(r.read().decode() or "null")
        except Exception:
            return None

    def now(self) -> float:
        return time.time()

    async def sleep(self, s: float) -> None:
        import asyncio
        if s > 0:
            await asyncio.sleep(s)


_host: Optional[Host] = None


def host() -> Host:
    global _host
    if _host is None:
        _host = Host()
    return _host


def set_host(h: Optional[Host]) -> None:
    """Tests: use a fake."""
    global _host
    _host = h
