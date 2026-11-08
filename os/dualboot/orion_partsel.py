#!/usr/bin/env python3
"""orion_partsel: which partitions may orion-install-disk touch? (os/image/files/usr/local/bin/orion-install-disk)

The installer used to assume "the two highest-numbered partitions are the new ones". With a gap in the numbering
(Windows recovery partitions are often 4 while 3 was deleted), `sfdisk --append` fills the gap, so the highest
numbers were someone else's partitions, and those got formatted. This file decides everything from `sfdisk -J`
instead, by sector:

  plan     the free region to use and the exact start/size of the two new partitions (boot, system)
  select   after sfdisk made them: the partitions that start exactly where planned, that didn't exist before, and
           with every old partition still exactly where it was. Anything else stops the install.
  esp      is there room on the EFI system partition (Windows makes them 100 MB; OrionOS needs about 50 MB free)?
  esp-node which partition is the EFI system partition
  disk     is a whole drive big enough for "erase" mode?

Everything here is pure (JSON in, answer out), so tests can feed it fake `sfdisk -J` output (os/dualboot/tests).
Exit codes: 0 fine, 1 refused (the reason is printed in plain words on stderr), 2 bad use.
"""
from __future__ import annotations

import argparse
import json
import sys
from typing import Any, Optional

ESP_TYPE = "c12a7328-f81f-11d2-ba4b-00a0c93ec93b"
ALIGN = 2048                      # 1 MiB in 512-byte sectors
MIB = 1024 * 1024
ESP_NEED_BYTES = 50 * MIB


class Refused(Exception):
    """A plain-words reason the installer must stop."""


# =====================================================================================  reading sfdisk -J
def load_table(text: str) -> dict[str, Any]:
    try:
        raw = json.loads(text)
    except ValueError:
        raise Refused("The partition table couldn't be read (sfdisk gave an answer that isn't JSON).")
    pt = raw.get("partitiontable") if isinstance(raw, dict) else None
    if not isinstance(pt, dict):
        raise Refused("The partition table couldn't be read.")
    ss = int(pt.get("sectorsize", 512) or 512)
    parts = []
    for p in pt.get("partitions") or []:
        parts.append({"node": str(p.get("node", "")), "start": int(p.get("start", 0)), "size": int(p.get("size", 0)),
                      "type": str(p.get("type", "")).lower(), "name": str(p.get("name", "")),
                      "uuid": str(p.get("uuid", "")).lower()})
    parts.sort(key=lambda p: p["start"])
    first = int(pt.get("firstlba", ALIGN) or ALIGN)
    last = pt.get("lastlba")
    if last is None:
        raise Refused("The partition table doesn't say where the drive ends (lastlba), so free space can't be "
                      "worked out safely.")
    return {"label": str(pt.get("label", "")), "device": str(pt.get("device", "")), "firstlba": first,
            "lastlba": int(last), "sectorsize": ss, "parts": parts}


def _align_up(n: int, a: int = ALIGN) -> int:
    return (n + a - 1) // a * a


def free_regions(table: dict[str, Any], align: int = ALIGN) -> list[tuple[int, int]]:
    """Unpartitioned regions as (start, size) in sectors, start and size rounded to whole MiB."""
    out = []
    cursor = table["firstlba"]
    edges = [(p["start"], p["start"] + p["size"]) for p in table["parts"]] + [(table["lastlba"] + 1, table["lastlba"] + 1)]
    for s, e in edges:
        if s > cursor:
            a = _align_up(cursor, align)
            size = (s - a) // align * align
            if size > 0:
                out.append((a, size))
        cursor = max(cursor, e)
    return out


def largest_free(table: dict[str, Any]) -> tuple[int, int]:
    regions = free_regions(table)
    return max(regions, key=lambda r: r[1]) if regions else (0, 0)


def overlaps(a_start: int, a_size: int, b_start: int, b_size: int) -> bool:
    return a_start < b_start + b_size and b_start < a_start + a_size


# =====================================================================================  plan and select
def plan_alongside(table: dict[str, Any], boot_mib: int = 1024, min_gb: int = 25,
                   esp_mib: int = 0) -> list[tuple[int, int]]:
    """The new partitions, inside the biggest free region: [(boot_start, boot_size), (root_start, root_size)], with
    an EFI system partition first when esp_mib is given (a whole, freshly erased drive)."""
    if table["label"] != "gpt":
        raise Refused("This drive doesn't use GPT (a very old setup), so OrionOS won't add partitions to it.")
    start, size = largest_free(table)
    ss = table["sectorsize"]
    if size * ss < min_gb * 1024 * MIB:
        have = size * ss // (1024 * MIB)
        raise Refused(f"Only {have} GB of free space in one piece on this drive; OrionOS needs {min_gb} GB. "
                      "In Windows, open Disk Management, shrink the C: drive by at least that much (right-click C: > "
                      "Shrink Volume), then run this again.")
    esp = esp_mib * MIB // ss
    boot = boot_mib * MIB // ss
    tail = ALIGN                                     # leave 1 MiB free at the end of the region
    root_start = start + esp + boot
    root_size = (size - esp - boot - tail) // ALIGN * ALIGN
    plan = ([(start, esp)] if esp else []) + [(start + esp, boot), (root_start, root_size)]
    check_plan(table, plan)
    return plan


def check_plan(table: dict[str, Any], plan: list[tuple[int, int]]) -> None:
    """Every planned partition must sit entirely in free space that existed before."""
    for s, z in plan:
        if z <= 0:
            raise Refused("The planned partition has no size.")
        for p in table["parts"]:
            if overlaps(s, z, p["start"], p["size"]):
                raise Refused(f"The planned partition at sector {s} would overlap {p['node']}. Nothing was changed.")
        if s < table["firstlba"] or s + z - 1 > table["lastlba"]:
            raise Refused("The planned partition would run past the usable part of the drive. Nothing was changed.")


def select_new(before: dict[str, Any], after: dict[str, Any], plan: list[tuple[int, int]]) -> list[str]:
    """Device names of the partitions sfdisk just made, in plan order, or Refused.

    Safe only if: every old partition is still there with the same start and size; each planned start has exactly
    one partition; that partition's device name and partition UUID are new; its size is what was asked (sfdisk may
    trim at most 1 MiB to fit); and it lies in space that was free before."""
    check_plan(before, plan)
    old = {(p["start"], p["size"]): p for p in before["parts"]}
    now = {(p["start"], p["size"]): p for p in after["parts"]}
    for key, p in old.items():
        if key not in now:
            raise Refused(f"{p['node']} moved or changed size while partitioning. Stopping before anything is "
                          "formatted; check the drive in Windows.")
    old_nodes = {p["node"] for p in before["parts"]}
    old_uuids = {p["uuid"] for p in before["parts"] if p["uuid"]}
    picked: list[str] = []
    for s, z in plan:
        hits = [p for p in after["parts"] if p["start"] == s]
        if len(hits) != 1:
            raise Refused(f"Couldn't find the new partition that should start at sector {s}. Stopping before "
                          "anything is formatted.")
        p = hits[0]
        if p["node"] in old_nodes or (p["uuid"] and p["uuid"] in old_uuids):
            raise Refused(f"The partition at sector {s} ({p['node']}) has the name or ID of one that was already "
                          "there. Stopping before anything is formatted.")
        if not (z - ALIGN <= p["size"] <= z):
            raise Refused(f"The new partition {p['node']} isn't the size that was asked for. Stopping before "
                          "anything is formatted.")
        if p["node"] in picked:
            raise Refused("Two planned partitions came out as the same device. Stopping.")
        picked.append(p["node"])
    extra = [p for p in after["parts"] if (p["start"], p["size"]) not in old and p["node"] not in picked]
    if extra:
        raise Refused(f"An unexpected partition appeared ({extra[0]['node']}). Stopping before anything is formatted.")
    return picked


# =====================================================================================  ESP and whole disk
def find_esp(table: dict[str, Any]) -> Optional[str]:
    for p in table["parts"]:
        if p["type"] == ESP_TYPE:
            return p["node"]
    return None


def esp_check(free_bytes: int, total_bytes: int = 0, need: int = ESP_NEED_BYTES) -> tuple[bool, str]:
    if free_bytes >= need:
        return True, f"The EFI system partition has {free_bytes // MIB} MB free."
    total = f" of its {total_bytes // MIB} MB" if total_bytes else ""
    return False, (f"The EFI system partition (where every system's start-up files live) has only {free_bytes // MIB} "
                   f"MB free{total}; OrionOS needs {need // MIB} MB there. Windows often makes it 100 MB. Nothing was "
                   "changed. To make room you would have to grow that partition (tools like GParted can, but it's "
                   "risky with Windows), or install OrionOS on a separate drive with \"Erase\".")


def disk_check(size_bytes: int, min_gb: int = 25) -> tuple[bool, str]:
    gb = size_bytes // (1024 * MIB)
    if gb >= min_gb:
        return True, f"The drive has {gb} GB."
    return False, f"That drive has only {gb} GB; OrionOS needs at least {min_gb} GB. Nothing was changed."


# =====================================================================================  command line
def _read(path: str) -> str:
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def _plan_arg(text: str) -> list[tuple[int, int]]:
    out = []
    for part in text.split(","):
        s, _, z = part.partition(":")
        out.append((int(s), int(z)))
    return out


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="orion_partsel")
    sub = ap.add_subparsers(dest="cmd")
    p = sub.add_parser("plan")
    p.add_argument("--table", required=True)
    p.add_argument("--boot-mib", type=int, default=1024)
    p.add_argument("--min-gb", type=int, default=25)
    p.add_argument("--esp-mib", type=int, default=0)
    p = sub.add_parser("select")
    p.add_argument("--before", required=True)
    p.add_argument("--after", required=True)
    p.add_argument("--plan", required=True, help="start:size,start:size")
    p = sub.add_parser("esp")
    p.add_argument("--free-bytes", type=int, required=True)
    p.add_argument("--total-bytes", type=int, default=0)
    p = sub.add_parser("esp-node")
    p.add_argument("--table", required=True)
    p = sub.add_parser("disk")
    p.add_argument("--size-bytes", type=int, required=True)
    p.add_argument("--min-gb", type=int, default=25)
    a = ap.parse_args(argv)
    try:
        if a.cmd == "plan":
            plan = plan_alongside(load_table(_read(a.table)), a.boot_mib, a.min_gb, a.esp_mib)
            print(" ".join(f"{s} {z}" for s, z in plan))
            return 0
        if a.cmd == "select":
            nodes = select_new(load_table(_read(a.before)), load_table(_read(a.after)), _plan_arg(a.plan))
            print("\n".join(nodes))
            return 0
        if a.cmd == "esp":
            ok, msg = esp_check(a.free_bytes, a.total_bytes)
            print(msg, file=sys.stdout if ok else sys.stderr)
            return 0 if ok else 1
        if a.cmd == "esp-node":
            node = find_esp(load_table(_read(a.table)))
            if not node:
                raise Refused("There's no EFI system partition on this drive, so there's no start-up menu to join.")
            print(node)
            return 0
        if a.cmd == "disk":
            ok, msg = disk_check(a.size_bytes, a.min_gb)
            print(msg, file=sys.stdout if ok else sys.stderr)
            return 0 if ok else 1
    except Refused as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except (OSError, ValueError) as exc:
        print(f"Couldn't read the partition information: {exc}", file=sys.stderr)
        return 1
    ap.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
