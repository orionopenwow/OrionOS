#!/usr/bin/env python3
"""OrionOS "Share files": send files between OrionOS, Windows, Android, iPhone and Mac on the same network, with
LocalSend (free, open source, Apache-2.0; https://localsend.org). The `orion-share` command:

    orion-share open                  open Share files (asks once to install LocalSend from Flathub, then opens it)
    orion-share status [--json]       LocalSend installed / running, firewall port open or closed
    orion-share install [--json]      install LocalSend now (the owner's action)
    orion-share firewall on|off|status [--json]
                                      the Settings toggle: keep LocalSend's port open to your home network (on), or
                                      only while Share files is open (off, the default)

The firewall (ufw) blocks everything coming in. LocalSend needs port 53317 (TCP and UDP) to be found and to
receive files, so Share files opens it, to the local network only, while LocalSend runs, and closes it when
LocalSend quits (orion-share-firewall.service; a boot-time reset closes it after a power cut).
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Optional

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import orion_apps as core  # noqa: E402

LOCALSEND = "org.localsend.localsend_app"
UNIT = "orion-share-firewall.service"
PORT = 53317
SYSTEMCTL = os.environ.get("ORION_SHARE_SYSTEMCTL", "systemctl")


def _ctl(*args: str) -> subprocess.CompletedProcess:
    try:
        return subprocess.run([SYSTEMCTL, *args], capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return subprocess.CompletedProcess([SYSTEMCTL, *args], 1, "", str(exc))


def firewall_open() -> bool:
    return _ctl("is-active", "--quiet", UNIT).returncode == 0


def firewall(on: bool) -> dict[str, Any]:
    r = _ctl("start" if on else "stop", UNIT)
    now = firewall_open()
    ok = r.returncode == 0 and now == on
    return {"ok": ok, "firewall": "open" if now else "closed", "port": PORT,
            "message": (f"LocalSend's port {PORT} is open to your home network." if now else
                        "LocalSend's port is closed; it opens only while Share files is running.")
            if ok else f"The firewall couldn't be changed: {(r.stderr or r.stdout).strip()[:300]}"}


def running(fp: core.Flatpak) -> bool:
    r = fp.run("ps", "--columns=application", timeout=15)
    return r.returncode == 0 and LOCALSEND in r.stdout.split()


def installed(fp: core.Flatpak) -> bool:
    return any(a["id"] == LOCALSEND for a in fp.installed("app"))


def status(fp: core.Flatpak) -> dict[str, Any]:
    inst = installed(fp) if fp.available() else False
    run = running(fp) if inst else False
    fw = firewall_open()
    msg = ("LocalSend isn't installed yet: open Share files to install it." if not inst else
           f"LocalSend is {'running' if run else 'installed'}; the sharing port is {'open' if fw else 'closed'}.")
    return {"ok": True, "installed": inst, "running": run, "firewall": "open" if fw else "closed", "port": PORT,
            "app": LOCALSEND, "message": msg}


def launch(fp: core.Flatpak) -> int:
    """Open LocalSend with the port open, and close the port when it quits (unless Settings keeps it open)."""
    if running(fp):
        # Already open (from another Share files): just bring it forward; that one closes the port later.
        subprocess.run([fp.binary, "run", LOCALSEND], timeout=30, capture_output=True)
        return 0
    opened_here = False
    if not firewall_open():
        opened_here = firewall(True)["ok"]
        if not opened_here:
            core.notify("Share files", "The firewall couldn't be opened, so other devices may not find this "
                                       "computer. Sending still works.")
    try:
        code = subprocess.call([fp.binary, "run", LOCALSEND])
        # LocalSend may hand off to an instance that's still starting: wait until it has really quit.
        while running(fp):
            time.sleep(3)
        return code
    finally:
        if opened_here:
            firewall(False)


def open_share(fp: core.Flatpak) -> int:
    if os.environ.get("ORION_SHARE_DRY_RUN") == "1":
        print("dry run: would open Share files")
        return 0
    if fp.available() and installed(fp):
        return launch(fp)
    os.execv(sys.executable, [sys.executable, str(HERE / "share_ui.py")])
    return 0


def _emit(data: dict[str, Any], as_json: bool) -> int:
    print(json.dumps(data, indent=1) if as_json else data.get("message", ""))
    return 0 if data.get("ok", True) else 1


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="orion-share", description="Share files on your network with LocalSend.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("open")
    sub.add_parser("launch", help=argparse.SUPPRESS)
    for name in ("status", "install"):
        sub.add_parser(name).add_argument("--json", action="store_true")
    s = sub.add_parser("firewall")
    s.add_argument("state", choices=["on", "off", "status"])
    s.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    fp = core.Flatpak()
    j = getattr(a, "json", False)
    if a.cmd == "open":
        return open_share(fp)
    if a.cmd == "launch":
        return launch(fp)
    if a.cmd == "status":
        return _emit(status(fp), j)
    if a.cmd == "install":
        return _emit(core.act_install(fp, LOCALSEND), j)
    if a.cmd == "firewall":
        if a.state == "status":
            fw = firewall_open()
            return _emit({"ok": True, "firewall": "open" if fw else "closed", "port": PORT,
                          "message": f"LocalSend's port is {'open' if fw else 'closed'}."}, j)
        return _emit(firewall(a.state == "on"), j)
    return 2


if __name__ == "__main__":
    sys.exit(main())
