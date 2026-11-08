"""Admin jobs from Settings without a terminal (H5).

Before: Settings opened a terminal and ran `su -P - orion-admin -c "sudo ..."`, so you typed the admin password in
a text window. Now, when the graphical password window (a polkit agent, started by the session) is running and the
job has a polkit action (polkit/ai.orion.admin.policy), Settings runs it with pkexec and the password window asks
for the admin password. Jobs that are themselves interactive (installing to the disk, making the vault, updates
with progress) still open a terminal, but the password is asked for in the window. Without a password window the
old terminal way is used, unchanged.

No GTK here, so it can be tested anywhere.
"""
from __future__ import annotations

import re
import shlex
from typing import Any, Optional, Sequence

from .backend import HELPERS, Result, plain_error

POLICY_FILE = "/usr/share/polkit-1/actions/ai.orion.admin.policy"
ADMIN_HELPER = str(HELPERS / "orion-settings-admin")
# program pkexec runs -> its polkit action (the exec.path in ai.orion.admin.policy must match exactly)
POLICY_TOOLS = {
    ADMIN_HELPER: "ai.orion.settings.admin",
    "/opt/orion/bin/orion-update": "ai.orion.admin.update",
    "/usr/local/bin/orion-slot": "ai.orion.admin.slot",
    "/usr/local/bin/orion-install-disk": "ai.orion.admin.install",
    "/usr/local/bin/orion-vault": "ai.orion.admin.vault",
}
AGENTS = ("polkit-gnome-authentication-agent-1", "lxpolkit", "polkit-mate-authentication-agent-1")


def agent_running(r: Any) -> bool:
    res = r.run(["pgrep", "-u", str(_uid()), "-f", "|".join(AGENTS)], timeout=5)
    return res.ok and bool(res.out.strip())


def _uid() -> int:
    import os
    return os.getuid()


def pkexec_ready(sys: Any) -> bool:
    """pkexec can ask for the admin password in a window right now."""
    return sys.has("pkexec") and sys.r.read(sys.root + POLICY_FILE) is not None and agent_running(sys.r)


def terminal_command(argv: Sequence[str], pkexec: bool) -> str:
    """The shell line a terminal runs for an admin job: pkexec (password in the window) when it can, else the old
    `su -P - orion-admin -c "sudo ..."` (password in the terminal). If pkexec can't find a password window after all
    (exit 127) the old way runs; if you press Cancel (126) nothing happens."""
    q = " ".join(shlex.quote(a) for a in argv)
    su = f"su -P - orion-admin -c {shlex.quote('sudo ' + q)}"
    if not pkexec or argv[0] not in POLICY_TOOLS:
        return su
    return (f"pkexec {q}; rc=$?; if [ $rc -eq 126 ]; then echo 'Cancelled: nothing changed.'; "
            f"elif [ $rc -eq 127 ]; then {su}; fi")


def has_password(r: Any, login: str) -> Optional[bool]:
    """`passwd -S` for your own account: P = has a password. None when it can't be told."""
    res = r.run(["passwd", "-S", login], timeout=5)
    m = re.match(r"^\S+\s+(\S+)", res.out or "")
    return (m[1] == "P") if res.ok and m else None


def password_problem(pw: str, again: str) -> Optional[str]:
    if pw != again:
        return "The two passwords aren't the same."
    if len(pw) < 8:
        return "Use at least 8 characters."
    if "\n" in pw or "\r" in pw:
        return "A password can't contain a line break."
    return None


def set_password(sys: Any, pw: str) -> tuple[bool, str]:
    """Set your own account's password through the admin helper (the window asks for the admin password)."""
    res: Result = sys.r.run_input(["pkexec", ADMIN_HELPER, "set-password"], pw + "\n", timeout=180)
    if res.ok:
        try:
            sys.conf.mkdir(parents=True, exist_ok=True)
            (sys.conf / "lock-ready").touch()
        except OSError:
            pass
        return True, "Password set. It unlocks the screen (Super+L) and the login screen."
    if res.rc == 126:
        return False, "Cancelled: nothing changed."
    return False, plain_error(res, "set the password")


def login_mode(sys: Any, *args: str) -> tuple[bool, str]:
    """auto | password | reduced-motion on|off, through the admin helper."""
    res = sys.r.run(["pkexec", ADMIN_HELPER, "login", *args], timeout=180)
    if res.ok:
        lines = [ln for ln in (res.out or "").splitlines() if ln.strip()]
        return True, lines[0] if lines else "Done."
    if res.rc == 126:
        return False, "Cancelled: nothing changed."
    return False, plain_error(res, "change how OrionOS starts")
