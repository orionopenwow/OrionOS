"""What "Undo the last update" and "Undo the last change" do on this computer.

There are three ways back, and the planner picks the right one:

  slot      the USB stick holds two copies of the system (orion-slot). Going back = starting the other copy.
            Used only when this orion-slot offers `rollback`.
  snapshot  a snapshot taken before the last update (orion-snapshot: btrfs on an installed drive, vault copies on
            the stick). Putting it back asks for your password in a terminal window.
  change    Orion's own change history (the daemon's /changes): app updates, settings Orion changed, and so on.
            Undoing a sensitive one asks your phone first, exactly as in the HUD.

"Undo the last update" prefers the whole-system ways (slot, then snapshot), then an update in the change history.
"Undo the last change" prefers the change history, then falls back to the snapshot.

Everything outside is reached through a Probe, so tests can fake it.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass, field
from typing import Any, Optional

from . import daemon_api

SLOT = "/usr/local/bin/orion-slot"
SNAPSHOT = "/usr/local/bin/orion-snapshot"


@dataclass
class UndoPlan:
    kind: str                      # "slot" | "snapshot" | "change" | "none"
    title: str                     # what the button says it will do
    detail: str = ""               # one plain sentence under it
    argv: list[str] = field(default_factory=list)   # run in a terminal (slot, snapshot)
    change_id: str = ""            # POST /changes/<id>/undo (change)

    @property
    def possible(self) -> bool:
        return self.kind != "none"


class Probe:
    """The real computer."""

    def profile(self) -> str:
        try:
            with open("/etc/orion/profile", encoding="utf-8") as f:
                return f.read().strip()
        except OSError:
            return ""

    def exists(self, path: str) -> bool:
        return os.access(path, os.X_OK)

    def slot_can_rollback(self) -> bool:
        # The rollback command belongs to the updates work (orion-slot); use it only once it is there.
        try:
            with open(SLOT, encoding="utf-8", errors="replace") as f:
                text = f.read()
        except OSError:
            return False
        return "\n  rollback)" in text or "\n  rollback|" in text

    def snapshot_status(self) -> Optional[dict[str, Any]]:
        if not self.exists(SNAPSHOT):
            return None
        try:
            r = subprocess.run([SNAPSHOT, "status", "--json"], capture_output=True, text=True, timeout=20)
            data = json.loads(r.stdout or "null")
        except (OSError, subprocess.SubprocessError, ValueError):
            return None
        return data if isinstance(data, dict) else None

    def changes(self) -> Optional[list[dict[str, Any]]]:
        data = daemon_api.request("GET", "/changes?limit=60")
        if isinstance(data, dict) and isinstance(data.get("changes"), list):
            return [c for c in data["changes"] if isinstance(c, dict)]
        return None


def _undoable(changes: Optional[list[dict[str, Any]]], updates_only: bool = False) -> Optional[dict[str, Any]]:
    for c in changes or []:                       # newest first (the daemon sorts them)
        if not c.get("undo") or c.get("undone_by") or c.get("undoes"):
            continue
        kind = str(c.get("kind") or "")
        if updates_only and "update" not in kind and "upgrade" not in kind:
            continue
        return c
    return None


def _slot_plan(p: Probe) -> Optional[UndoPlan]:
    if p.profile() == "image" and p.exists(SLOT) and p.slot_can_rollback():
        return UndoPlan("slot", "Go back to the previous OrionOS version",
                        "The stick starts the system copy you had before the last update. Your files stay as they "
                        "are. The computer restarts.", argv=["pkexec", SLOT, "rollback"])
    return None


def _snapshot_plan(p: Probe) -> Optional[UndoPlan]:
    st = p.snapshot_status()
    if st and st.get("undo_to"):
        what = ("the system as it was before the last update" if st.get("backend") == "btrfs"
                else "your settings and Orion's data as they were before the last update")
        return UndoPlan("snapshot", "Put back the snapshot from before the last update",
                        f"Puts back {what}. It asks for your password, and the change can itself be undone.",
                        argv=["pkexec", SNAPSHOT, "undo"])
    return None


def _change_plan(c: Optional[dict[str, Any]]) -> Optional[UndoPlan]:
    if not c:
        return None
    what = str(c.get("what") or "the last change")[:120]
    label = str((c.get("undo") or {}).get("label") or "")
    sensitive = bool((c.get("undo") or {}).get("sensitive"))
    detail = f"Undoes: {what}." + (" Your phone asks you to approve it first." if sensitive else "")
    if label and label.lower() not in detail.lower():
        detail += f" ({label})"
    return UndoPlan("change", f"Undo “{what}”", detail, change_id=str(c.get("id") or ""))


NOTHING_UPDATE = UndoPlan("none", "Nothing to undo yet",
                          "There's no update this computer can go back from: no snapshot from before one, and no "
                          "update in Orion's change history.")
NOTHING_CHANGE = UndoPlan("none", "Nothing to undo yet",
                          "Orion's change history has nothing that can be undone, and there's no snapshot from "
                          "before an update.")


def plan_last_update(p: Optional[Probe] = None) -> UndoPlan:
    p = p or Probe()
    return (_slot_plan(p) or _snapshot_plan(p) or _change_plan(_undoable(p.changes(), updates_only=True))
            or NOTHING_UPDATE)


def plan_last_change(p: Optional[Probe] = None) -> UndoPlan:
    p = p or Probe()
    return _change_plan(_undoable(p.changes())) or _snapshot_plan(p) or NOTHING_CHANGE


def terminal_command(plan: UndoPlan) -> list[str]:
    """The slot and snapshot ways run in a terminal window, so the password prompt (pkexec's own) works even when
    no graphical password helper is running. The window stays open until Enter."""
    import shlex
    cmd = " ".join(shlex.quote(a) for a in plan.argv)
    script = (f"{cmd}; rc=$?; echo; if [ $rc -eq 0 ]; then echo 'Done.'; else echo 'That did not work (nothing "
              "else was changed).'; fi; printf 'Press Enter to close. '; read _")
    term = shutil.which("foot") or shutil.which("x-terminal-emulator") or "foot"
    return [term, "--title=orion-tool", "sh", "-c", script] if term.endswith("foot") else [term, "-e", "sh", "-c",
                                                                                          script]


def run_change_undo(plan: UndoPlan) -> tuple[bool, str]:
    res = daemon_api.request("POST", f"/changes/{plan.change_id}/undo", {}, timeout=60)
    if not isinstance(res, dict):
        return False, "Orion's brain isn't answering, so the change history can't be used right now."
    msg = str(res.get("message") or res.get("error") or "")
    if res.get("pending"):
        return True, msg or "Waiting for your approval on your phone."
    return bool(res.get("ok")), msg or ("Undone." if res.get("ok") else "That couldn't be undone.")


def run(plan: UndoPlan) -> tuple[bool, str]:
    """Carry out a plan. Slot/snapshot open a terminal window and return straight away."""
    if plan.kind == "change":
        return run_change_undo(plan)
    if plan.kind in ("slot", "snapshot"):
        try:
            subprocess.Popen(terminal_command(plan), start_new_session=True)   # noqa: S603 (fixed argv)
        except OSError:
            return False, "Couldn't open a terminal window."
        return True, "Follow the steps in the terminal window (it asks for your password)."
    return False, plan.detail
