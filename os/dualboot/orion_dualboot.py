#!/usr/bin/env python3
"""orion_dualboot: living next to Windows (review H14, feature 3).

  status [--json]          is there a Windows to restart into, and how (no admin rights needed)
  restart-windows [--dry-run]
                           (root, through orion-dualboot-helper + polkit) start Windows once at the next restart,
                           then restart. OrionOS stays the normal choice afterwards.
  boot-order --efibootmgr FILE --new NUM --first windows|orionos
                           (orion-install-disk) the firmware's boot order with OrionOS's new entry placed
  entries --efibootmgr FILE --label NAME
                           (orion-install-disk) the firmware entries with exactly that name
  windows-parts [--json]   the Windows partitions on this computer (for "Open Windows files")

How Windows is started once:
  1. The firmware's own "BootNext" (efibootmgr -n): the computer starts the Windows Boot Manager directly, exactly as
     if OrionOS weren't there. This is BitLocker-safe: nothing between the firmware and Windows changes, so Windows
     doesn't ask for its recovery key.
  2. Only if the firmware has no Windows entry: GRUB's one-time choice (grub-reboot) on an installed OrionOS. GRUB
     then hands over to Windows ("chainloading"); with BitLocker that can make Windows ask for its recovery key
     once, because the start-up chain looks different to the computer's security chip.

Tests: every outside command and file can be swapped with ORION_DB_* environment variables.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from typing import Any, Optional


class Env:
    def __init__(self, environ: Optional[dict[str, str]] = None) -> None:
        e = os.environ if environ is None else environ
        self.efibootmgr = e.get("ORION_DB_EFIBOOTMGR", "efibootmgr")
        self.grub_reboot = e.get("ORION_DB_GRUB_REBOOT", "grub-reboot")
        self.systemctl = e.get("ORION_DB_SYSTEMCTL", "systemctl")
        self.lsblk = e.get("ORION_DB_LSBLK", "lsblk")
        self.grubcfg = e.get("ORION_DB_GRUBCFG", "/boot/grub/grub.cfg")
        self.cmdline = e.get("ORION_DB_CMDLINE", "/proc/cmdline")
        self.efivars = e.get("ORION_DB_EFIVARS", "/sys/firmware/efi")

    def run(self, argv: list[str], timeout: float = 60) -> subprocess.CompletedProcess:
        try:
            return subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
        except (FileNotFoundError, PermissionError):
            return subprocess.CompletedProcess(argv, 127, "", f"{argv[0]}: not installed")
        except subprocess.TimeoutExpired:
            return subprocess.CompletedProcess(argv, 124, "", f"{argv[0]}: took too long")


def read(path: str) -> str:
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except OSError:
        return ""


# =====================================================================================  firmware boot entries
def parse_efibootmgr(text: str) -> dict[str, Any]:
    out: dict[str, Any] = {"next": "", "current": "", "order": [], "entries": {}}
    for line in (text or "").splitlines():
        m = re.match(r"^BootNext:\s*([0-9A-Fa-f]{4})", line)
        if m:
            out["next"] = m.group(1).upper()
        m = re.match(r"^BootCurrent:\s*([0-9A-Fa-f]{4})", line)
        if m:
            out["current"] = m.group(1).upper()
        m = re.match(r"^BootOrder:\s*([0-9A-Fa-f,]*)", line)
        if m:
            out["order"] = [x.upper() for x in m.group(1).split(",") if x]
        m = re.match(r"^Boot([0-9A-Fa-f]{4})(\*?)\s+(.*)$", line)
        if m:
            label = re.split(r"\t|\s{2,}|\s+(?=HD\(|PciRoot\(|VenHw\(|BBS\()", m.group(3).strip())[0].strip()
            out["entries"][m.group(1).upper()] = {"label": label, "active": m.group(2) == "*"}
    return out


def windows_entry(info: dict[str, Any]) -> str:
    for num, e in info["entries"].items():
        if e["label"].lower().startswith("windows boot manager"):
            return num
    return ""


def entries_named(info: dict[str, Any], label: str) -> list[str]:
    return [n for n, e in info["entries"].items() if e["label"].lower() == label.lower()]


def boot_order(order: list[str], new: str, first: str, win: str) -> list[str]:
    """The firmware's boot order with OrionOS's entry `new` placed: right after Windows (Windows keeps starting by
    default; OrionOS is picked from the firmware's boot menu or with "Restart into OrionOS"), or first."""
    rest = [x for x in order if x != new]
    if first == "orionos" or not win or win not in rest:
        return [new] + rest
    i = rest.index(win)
    return rest[:i + 1] + [new] + rest[i + 1:]


# =====================================================================================  GRUB
def grub_windows_entry(cfg: str) -> str:
    """The id (or else the title) of the Windows entry os-prober put in grub.cfg."""
    for line in (cfg or "").splitlines():
        m = re.match(r"""^\s*menuentry\s+(['"])(.*?)\1(.*)\{\s*$""", line)
        if not m or "windows" not in m.group(2).lower():
            continue
        idm = re.search(r"""\$menuentry_id_option\s+(['"])(.*?)\1""", m.group(3))
        return idm.group(2) if idm else m.group(2)
    return ""


# =====================================================================================  Windows partitions
def windows_parts(lsblk_json: str) -> list[dict[str, Any]]:
    try:
        data = json.loads(lsblk_json or "{}")
    except ValueError:
        return []
    out = []

    def walk(devs: list[dict[str, Any]]) -> None:
        for d in devs or []:
            fs = (d.get("fstype") or "").lower()
            ptype = (d.get("parttype") or "").lower()
            if ptype == "de94bba4-06d1-4d40-a16a-bfd50179d6ac":          # Windows recovery: never shown
                fs = ""
            if d.get("type") == "part" and fs in ("bitlocker", "ntfs"):
                size = int(d.get("size") or 0)
                if size >= 8 * 1024 ** 3:                                   # C:-sized, not the small helpers
                    out.append({"path": d.get("path") or "", "fstype": fs, "bitlocker": fs == "bitlocker",
                                "size": size, "label": d.get("label") or "",
                                "mounted": [m for m in (d.get("mountpoints") or []) if m]})
            walk(d.get("children") or [])

    walk(data.get("blockdevices") or [])
    return out


# =====================================================================================  status and restart
def on_stick(env: Env) -> bool:
    return "orion.slot=" in read(env.cmdline)


def status(env: Env) -> dict[str, Any]:
    efi = os.path.isdir(env.efivars)
    info = parse_efibootmgr(env.run([env.efibootmgr]).stdout) if efi else parse_efibootmgr("")
    win = windows_entry(info)
    if win:
        return {"windows": True, "method": "bootnext", "entry": win,
                "words": "Restarts straight into Windows once (BitLocker-safe). OrionOS stays the normal choice."}
    grub = "" if on_stick(env) else grub_windows_entry(read(env.grubcfg))
    if grub:
        return {"windows": True, "method": "grub", "entry": grub,
                "words": "Restarts into Windows once through OrionOS's start menu. If Windows uses BitLocker it "
                         "may ask for its recovery key that one time."}
    why = ("This computer doesn't start in UEFI mode, and no Windows was found in OrionOS's start menu."
           if not efi else "No Windows was found on this computer.")
    return {"windows": False, "method": None, "entry": "", "words": why}


def restart_windows(env: Env, dry_run: bool = False) -> dict[str, Any]:
    st = status(env)
    if not st["windows"]:
        return {"ok": False, "message": st["words"]}
    if st["method"] == "bootnext":
        argv = [env.efibootmgr, "-q", "-n", st["entry"]]
    else:
        argv = [env.grub_reboot, st["entry"]]
    if not dry_run:
        r = env.run(argv)
        if r.returncode != 0:
            return {"ok": False, "message": "Couldn't set Windows for the next start: " +
                    ((r.stderr or r.stdout or "").strip()[-200:] or f"exit {r.returncode}")}
        env.run([env.systemctl, "reboot"])
    return {"ok": True, "method": st["method"], "ran": argv,
            "message": "Restarting into Windows. Next time, OrionOS starts as usual."}


def main(argv: Optional[list[str]] = None, env: Optional[Env] = None) -> int:
    env = env or Env()
    ap = argparse.ArgumentParser(prog="orion_dualboot")
    sub = ap.add_subparsers(dest="cmd")
    s = sub.add_parser("status")
    s.add_argument("--json", action="store_true")
    s = sub.add_parser("restart-windows")
    s.add_argument("--dry-run", action="store_true")
    s.add_argument("--json", action="store_true")
    s = sub.add_parser("boot-order")
    s.add_argument("--efibootmgr", required=True)
    s.add_argument("--new", required=True)
    s.add_argument("--first", choices=["windows", "orionos"], default="windows")
    s = sub.add_parser("entries")
    s.add_argument("--efibootmgr", required=True)
    s.add_argument("--label", required=True)
    s = sub.add_parser("windows-parts")
    s.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    if a.cmd == "status":
        st = status(env)
        print(json.dumps(st) if a.json else st["words"])
        return 0 if st["windows"] else 1
    if a.cmd == "restart-windows":
        if os.geteuid() != 0 and not a.dry_run:
            print("This needs admin rights: use orion-restart-windows.", file=sys.stderr)
            return 2
        res = restart_windows(env, a.dry_run)
        print(json.dumps(res) if a.json else res["message"], file=sys.stdout if res["ok"] else sys.stderr)
        return 0 if res["ok"] else 1
    if a.cmd == "boot-order":
        info = parse_efibootmgr(read(a.efibootmgr))
        print(",".join(boot_order(info["order"], a.new.upper(), a.first, windows_entry(info))))
        return 0
    if a.cmd == "entries":
        print("\n".join(entries_named(parse_efibootmgr(read(a.efibootmgr)), a.label)))
        return 0
    if a.cmd == "windows-parts":
        r = env.run([env.lsblk, "-J", "-b", "-o", "PATH,TYPE,FSTYPE,PARTTYPE,SIZE,LABEL,MOUNTPOINTS"])
        parts = windows_parts(r.stdout)
        if a.json:
            print(json.dumps(parts))
        else:
            for p in parts:
                print(p["path"], "BitLocker" if p["bitlocker"] else "NTFS", f"{p['size'] // 1024 ** 3} GB",
                      p["label"])
        return 0 if parts else 1
    ap.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
