"""The login screen's decisions, without GTK (tested in os/greeter/tests/test_greeter_logic.py).

The screen offers two ways in:
  "Start Orion again"   the normal Orion session (SESSION_CMD, default /usr/local/bin/orion-session)
  "Rescue desktop"      plain Sway with a terminal, files and a browser (RESCUE_CMD, os/rescue)

LOGIN_MODE (in /etc/orion/login.conf) says whether a password is asked:
  password  the field is shown; either button starts the chosen session once the password is right
  auto      no field: the buttons start straight away (PAM lets the login user in without a password only in
            this mode, see orion-login-auto-ok). If PAM asks for a password anyway, the field appears.
"""
from __future__ import annotations

import os

CONF = "/etc/orion/login.conf"
DEFAULT_SESSION = "/usr/local/bin/orion-session"
DEFAULT_RESCUE = "/opt/orion/rescue/orion-rescue-session"
TARGETS = ("orion", "rescue")


def read_conf(path: str = CONF) -> dict[str, str]:
    out: dict[str, str] = {}
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                out[k.strip()] = v.strip().strip('"').strip("'")
    except OSError:
        pass
    return out


def login_mode(conf: dict[str, str]) -> str:
    return "password" if conf.get("LOGIN_MODE", "auto").strip().lower() == "password" else "auto"


def session_cmd(conf: dict[str, str], target: str, exists=os.path.exists) -> list[str]:
    """The command greetd starts for the chosen button. An unknown target, or a rescue session that isn't
    installed, falls back to the normal session (never to nothing)."""
    normal = (conf.get("SESSION_CMD") or DEFAULT_SESSION).split()
    if target == "rescue":
        rescue = (conf.get("RESCUE_CMD") or DEFAULT_RESCUE).split()
        if rescue and exists(rescue[0]):
            return rescue
    return normal


def session_env(target: str) -> list[str]:
    # The black-hole hand-off only makes sense into the Orion screen.
    return ["ORION_LOGIN_HANDOFF=blackhole"] if target != "rescue" else ["ORION_RESCUE_SESSION=1"]


def page_config(conf: dict[str, str], user_info: dict, exists=os.path.exists) -> dict:
    """What the page is told at start (window.ORION_GREETER)."""
    rescue = (conf.get("RESCUE_CMD") or DEFAULT_RESCUE).split()
    return dict(user_info,
                reducedMotion=conf.get("REDUCED_MOTION", "0").lower() in ("1", "yes", "true", "on"),
                mode=login_mode(conf),
                rescue=bool(rescue) and exists(rescue[0]))


def clean_target(value: object) -> str:
    return value if isinstance(value, str) and value in TARGETS else "orion"
