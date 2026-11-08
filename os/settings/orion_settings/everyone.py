"""Orion Settings, the "Everyone can use it" side (complete plan step 6; os/everyone/README.md): Accessibility,
Languages & typing, the guest account and extra people, and battery care on the Power page.

Every read and change goes through the OrionOS commands orion-access, orion-typing, orion-power and orion-people
(os/everyone/orion_everyone.py), so this file only builds command lines and reads their JSON. No GTK here: the
pages are in everyone_pages.py, and tests use the fake runner."""
from __future__ import annotations

import re
import shlex
from typing import TYPE_CHECKING, Any, Optional

from . import parsers as P
from .backend import plain_error

if TYPE_CHECKING:
    from .backend import System

GUEST_START = "/opt/orion/everyone/orion-guest-start"
PEOPLE_ADMIN = "/opt/orion/everyone/orion-people-admin"
LOGIN_RE = re.compile(r"^[a-z][a-z0-9-]{1,30}$")
TEXT_SIZES = [("1", "Normal"), ("1.25", "Large (125%)"), ("1.5", "Larger (150%)"), ("1.75", "Very large (175%)"),
              ("2", "Largest (200%)")]
SCREEN_SIZES = [("off", "Normal"), ("1.25", "125%"), ("1.5", "150%"), ("1.75", "175%"), ("2", "200%")]
HUD_ZOOMS = [("1", "100%"), ("1.25", "125%"), ("1.5", "150%"), ("2", "200%")]


def _json(sys: "System", argv: list[str], timeout: float = 10) -> Optional[dict[str, Any]]:
    if not sys.has(argv[0]):
        return None
    data = P.parse_json_maybe(sys.r.run(argv, timeout=timeout).out)
    return data if isinstance(data, dict) else None


def _do(sys: "System", argv: list[str], what: str, timeout: float = 20) -> tuple[bool, str]:
    r = sys.r.run(argv, timeout=timeout, env={"ORION_QUIET": "1"})   # Settings shows its own message
    if r.ok:
        lines = [ln for ln in r.out.strip().splitlines() if ln.strip()]
        return True, lines[-1] if lines else "Saved."
    return False, plain_error(r, what)


# ------------------------------------------------------------------ Accessibility
def access(sys: "System") -> dict[str, Any]:
    st = _json(sys, ["orion-access", "status", "--json"])
    anim = sys.r.run(["gsettings", "get", "org.gnome.desktop.interface", "enable-animations"]).out.strip()
    return {"available": st is not None, **(st or {}), "animations": anim != "false",
            "orca": sys.has("orca"), "osk": sys.has("wvkbd-mobintl"), "boot_sound": sys.boot_sound()}


def set_access(sys: "System", what: str, value: str) -> tuple[bool, str]:
    if what not in ("screen-reader", "keyboard", "keyboard-touch", "contrast", "text-size", "screen-size",
                    "hud-zoom") or not re.fullmatch(r"on|off|[0-9.]{1,4}", value):
        return False, "That choice can't be used."
    return _do(sys, ["orion-access", what, value], "change that")


# ------------------------------------------------------------------ Languages & typing
def typing(sys: "System") -> dict[str, Any]:
    st = _json(sys, ["orion-typing", "status", "--json"])
    return {"available": st is not None, **(st or {})}


def typing_change(sys: "System", action: str, lang: str) -> tuple[bool, str]:
    if action not in ("add", "remove") or not re.fullmatch(r"[A-Za-z0-9_-]{2,40}", lang):
        return False, "That language can't be used."
    return _do(sys, ["orion-typing", action, lang], f"{action} the language")


# ------------------------------------------------------------------ Power (battery care)
def power(sys: "System") -> dict[str, Any]:
    return {"care": _json(sys, ["orion-power", "status", "--json"])}


def set_power_pref(sys: "System", key: str, on: bool) -> tuple[bool, str]:
    if key not in ("auto-saver", "hibernate"):
        return False, "That choice can't be used."
    ok, msg = _do(sys, ["orion-power", key, "on" if on else "off"], "save the power setting")
    if not ok or key != "hibernate":
        return ok, msg
    # Closing the lid on battery follows it: sleep, then hibernate after 2 hours (or plain sleep again).
    ok, msg = sys.set_logind("HandleLidSwitch", "suspend-then-hibernate" if on else "suspend")
    return ok, ("Sleep, then hibernate after 2 hours." if on else "Plain sleep.") if ok else msg


# ------------------------------------------------------------------ People
def people(sys: "System") -> dict[str, Any]:
    st = _json(sys, ["orion-people", "status", "--json"])
    return {"available": st is not None, **(st or {})}


def admin_command(args: list[str]) -> str:
    """A command for the terminal that runs the people helper through the orion-admin account (asks its password)."""
    inner = "sudo " + " ".join(shlex.quote(a) for a in [PEOPLE_ADMIN, *args])
    return f"su -P - orion-admin -c {shlex.quote(inner)}"


def guest_command(on: bool) -> str:
    return admin_command(["guest", "on" if on else "off"])


def add_person_command(login: str, full: str) -> Optional[str]:
    login = (login or "").strip()
    full = re.sub(r"[\r\n:,]", "", full or "").strip()[:60]
    if not LOGIN_RE.match(login) or login in ("guest", "root", "admin") or login.startswith("orion"):
        return None
    return admin_command(["add", login, full or login])


def start_guest(sys: "System") -> tuple[bool, str]:
    r = sys.r.run(["pkexec", GUEST_START], timeout=60)
    if not r.ok:
        return False, plain_error(r, "start a guest session")
    sys.launch(["swaymsg", "exit"])
    return True, "Logging out. The guest session starts next."
