#!/usr/bin/python3
"""Writes greetd's /etc/greetd/config.toml for OrionOS (used by provision.sh and orion-login-mode).

Whatever the mode, greetd's login screen (default_session) is Orion's graphical login screen, with "Start Orion
again" and "Rescue desktop" (orion-greeter-start). It falls back to the text login (agreety) only when the
graphical one can't run. So logging out, or Sway crashing, never leaves anyone at a bare text prompt.

    auto      at power-on Orion starts straight away (initial_session: the VM lab, the USB stick whose vault
              passphrase already protects it, and a fresh install until first-run sets a password)
    password  no initial_session: the login screen asks for the password first

    greetd_config.py write auto|password [--path /etc/greetd/config.toml] [--user orion]
    greetd_config.py show auto|password
"""
from __future__ import annotations

import os
import sys

GREETER_START = "/opt/orion/greeter/orion-greeter-start"
LOGIN_NEXT = "/opt/orion/greeter/orion-login-next"   # os/everyone: "Start a guest session" goes in front
SESSION = "/usr/local/bin/orion-session"
CONFIG = "/etc/greetd/config.toml"


def _q(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def render(mode: str, user: str = "orion", login_next: bool = False) -> str:
    if mode not in ("auto", "password"):
        raise ValueError("mode must be auto or password")
    if not user or not all(c.isalnum() or c in "-_." for c in user):
        raise ValueError("bad user name")
    greeter = f"{LOGIN_NEXT} {GREETER_START}" if login_next else GREETER_START
    head = ("# OrionOS: straight into Orion at power-on; after logging out (or if the session stops) the login screen\n"
            "# offers \"Start Orion again\" and \"Rescue desktop\". Written by os/greeter/greetd_config.py.\n"
            if mode == "auto" else
            "# OrionOS: the login screen asks for your password, then starts Orion (or the Rescue desktop).\n"
            "# Written by os/greeter/greetd_config.py. If it can't run, it falls back to the text login (agreety).\n")
    out = [head, "[terminal]", "vt = 1", "", "[default_session]", f"command = {_q(greeter)}", 'user = "_greetd"']
    if mode == "auto":
        out += ["", "[initial_session]", f"command = {_q('env ORION_LOGIN_HANDOFF=blackhole ' + SESSION)}",
                f"user = {_q(user)}"]
    return "\n".join(out) + "\n"


def write(mode: str, path: str = CONFIG, user: str = "orion") -> None:
    text = render(mode, user, login_next=os.path.exists(LOGIN_NEXT))
    d = os.path.dirname(path) or "."
    os.makedirs(d, exist_ok=True)
    tmp = path + ".orion-new"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    os.chmod(tmp, 0o644)
    os.replace(tmp, path)


def main(argv: list[str]) -> int:
    if len(argv) < 2 or argv[0] not in ("write", "show") or argv[1] not in ("auto", "password"):
        print(__doc__.strip(), file=sys.stderr)
        return 2
    path, user = CONFIG, "orion"
    rest = argv[2:]
    for i, a in enumerate(rest):
        if a == "--path" and i + 1 < len(rest):
            path = rest[i + 1]
        if a == "--user" and i + 1 < len(rest):
            user = rest[i + 1]
    if argv[0] == "show":
        sys.stdout.write(render(argv[1], user, login_next=os.path.exists(LOGIN_NEXT)))
        return 0
    write(argv[1], path, user)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
