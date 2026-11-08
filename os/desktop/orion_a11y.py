"""The screen reader (Orca) on Sway, with the Orion HUD (OrionOS 1.0 plan, B. "Accessibility"; risk "Accessibility on
Sway is weaker than on GNOME: test Orca + AT-SPI early").

Orca was made for GNOME. On Sway it works when four things are true, and this file checks and fixes them:
  1. the session bus is there and the user's systemd/D-Bus know this session's WAYLAND_DISPLAY (orion-session-start
     imports them), so the accessibility bus (at-spi-bus-launcher, org.a11y.Bus) can be started on demand;
  2. the accessibility bus answers (asking it for its address starts it) before Orca starts;
  3. nothing turns the bridge off: NO_AT_BRIDGE=1 or GTK_A11Y=none in the session (we use them in screenshot
     tests only) silence GTK apps and the HUD's WebKitGTK window;
  4. Qt and Chromium/Electron apps are told to talk to it (QT_ACCESSIBILITY=1, ACCESSIBILITY_ENABLED=1:
     orion_everyone.session_env_for_reader writes them).

    orion-a11y-check [--json]     what works and what doesn't, in plain words (exit 0 when Orca can work)
    orion-a11y-check start        make sure the accessibility bus is up, then start Orca
    orion-a11y-check bus-up       only start the accessibility bus (used by orion-access before Orca)

The HUD registers on the bus as "Orion" (WebKitGTK); with python3-pyatspi installed the check lists it.
"""
from __future__ import annotations

from typing import Any, Callable, Optional

Run = Callable[[list[str]], tuple[int, str]]

DISABLERS = {"NO_AT_BRIDGE": "1", "GTK_A11Y": "none"}
BUS_CALL = ["busctl", "--user", "call", "org.a11y.Bus", "/org/a11y/bus", "org.a11y.Bus", "GetAddress"]
BUS_CALL_DBUS_SEND = ["dbus-send", "--session", "--print-reply", "--dest=org.a11y.Bus", "/org/a11y/bus",
                      "org.a11y.Bus.GetAddress"]
HUD_NAMES = ("orion", "orion-desktop", "com.orion.desktop")


def row(cid: str, ok: bool, text: str, fix: str = "", needed: bool = True) -> dict[str, Any]:
    return {"id": cid, "ok": bool(ok), "text": text, "fix": fix if not ok else "", "needed": needed}


def disabled_by(env: dict[str, str]) -> list[str]:
    """Environment settings that switch the accessibility bridge off."""
    return [f"{k}={env[k]}" for k, v in DISABLERS.items() if env.get(k, "").strip().lower() == v]


def strip_disablers(text: str) -> str:
    """session.env without lines that switch the bridge off (other lines kept exactly)."""
    out = []
    for ln in text.splitlines():
        s = ln.strip()
        if s.startswith("export "):
            s = s[7:].strip()
        key, _, val = s.partition("=")
        if key in DISABLERS and val.strip().strip('"\'').lower() == DISABLERS[key]:
            continue
        out.append(ln)
    return "\n".join(out) + ("\n" if out else "")


def bus_up(run: Run) -> tuple[bool, str]:
    """Ask the accessibility bus for its address (this starts it). (ok, address or why not)."""
    for argv in (BUS_CALL, BUS_CALL_DBUS_SEND):
        rc, out = run(argv)
        if rc == 0 and "unix:" in out:
            return True, out.strip().split('"')[1] if '"' in out else out.strip()
    return False, "the accessibility bus didn't answer"


def parse_apps(names: list[str]) -> bool:
    return any(n.strip().lower() in HUD_NAMES or n.strip().lower().startswith("orion") for n in names)


def hud_on_bus() -> Optional[bool]:
    """Is the Orion window registered with the accessibility bus? None when pyatspi isn't installed."""
    try:
        import pyatspi  # python3-pyatspi
        d = pyatspi.Registry.getDesktop(0)
        return parse_apps([d.getChildAtIndex(i).name or "" for i in range(d.childCount)])
    except Exception:
        return None


def check(env: dict[str, str], run: Run, has: Callable[[str], bool], session_env: str = "",
          reader_on: bool = False, orca_running: Optional[bool] = None,
          hud: Optional[bool] = None) -> list[dict[str, Any]]:
    rows = [
        row("orca", has("orca"), "Orca is installed." if has("orca") else "Orca isn't installed.",
            "sudo apt install orca"),
        row("atspi", has("at-spi-bus-launcher") or has("/usr/libexec/at-spi-bus-launcher"),
            "The accessibility service is installed.", "sudo apt install at-spi2-core"),
        row("session_bus", bool(env.get("DBUS_SESSION_BUS_ADDRESS")) or bool(env.get("XDG_RUNTIME_DIR")),
            "This session has a message bus.", "Log out and back in (the Orion session starts it)."),
        row("wayland", bool(env.get("WAYLAND_DISPLAY")), "Running in the Orion desktop (Wayland).",
            "Run this inside the Orion desktop, not over SSH."),
    ]
    ok, why = bus_up(run)
    rows.append(row("bus", ok, "The accessibility bus answers." if ok else f"Problem: {why}.",
                    "systemctl --user import-environment WAYLAND_DISPLAY DBUS_SESSION_BUS_ADDRESS, then try again"))
    bad = disabled_by(env) + [f"{d} (in session.env)" for d in disabled_by(_env_lines(session_env))]
    rows.append(row("bridge", not bad, "Nothing switches the accessibility bridge off." if not bad else
                    "The bridge is switched off by " + ", ".join(bad) + ".",
                    "orion-a11y-check fix, then log out and back in"))
    qt = _env_lines(session_env).get("QT_ACCESSIBILITY") == "1"
    rows.append(row("qt", qt or not reader_on, "Qt and Electron apps talk to the screen reader." if qt else
                    "Qt and Electron apps will talk to it after the next log-in.",
                    "Log out and back in once.", needed=False))
    if reader_on:
        rows.append(row("orca_running", bool(orca_running), "Orca is running." if orca_running else
                        "The screen reader is on but Orca isn't running.", "orion-a11y-check start"))
    if hud is not None:
        rows.append(row("hud", hud, "The Orion window is readable by the screen reader." if hud else
                        "The Orion window isn't on the accessibility bus yet.", "Restart the Orion screen (Super+O)",
                        needed=False))
    return rows


def verdict(rows: list[dict[str, Any]]) -> tuple[bool, str]:
    bad = [r for r in rows if not r["ok"] and r["needed"]]
    if not bad:
        return True, "The screen reader can work here."
    return False, "The screen reader needs: " + "; ".join(r["fix"] or r["text"] for r in bad)


def _env_lines(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for ln in (text or "").splitlines():
        s = ln.strip()
        if s.startswith("export "):
            s = s[7:].strip()
        if "=" in s and not s.startswith("#"):
            k, _, v = s.partition("=")
            out[k.strip()] = v.strip().strip('"\'')
    return out
