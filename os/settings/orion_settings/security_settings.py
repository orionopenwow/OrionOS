"""Settings > Security check-up, the part without GTK (so it can be tested): reads and changes Orion's
Exposure Map, Host Health, Supply-Chain Scan and Dependency Autopilot through the daemon's routes
(apps/daemon/security_toolkit.py), in plain words."""
from __future__ import annotations

from typing import Any, Optional

from .backend import API
from .backups import ago

REACH = {"local": "this computer only", "lan": "home network", "tailscale": "Tailscale", "internet": "internet"}
REACH_PILL = {"local": "", "lan": "warn", "tailscale": "ok", "internet": "warn"}


def load(r: Any) -> Optional[dict[str, Any]]:
    st = r.http("GET", f"{API}/security/status")
    return st if isinstance(st, dict) and "config" in st else None


def exposure_rows(st: dict[str, Any], machine: str = "") -> list[tuple[str, str, str, str]]:
    """(title, subtitle, pill text, pill kind) per listener others can reach, widest first; then a line for the
    ones on this computer only."""
    maps = (st.get("exposure") or {}).get("maps") or {}
    m = maps.get(machine or st.get("machine", "")) or {}
    rows = m.get("listeners") or []
    out = []
    for r in rows:
        if r.get("widest") == "local":
            continue
        prog = r.get("process") or "unknown program"
        out.append((f"{prog} on port {r['port']}/{r['proto']}", f"Listens on {r.get('bind', '')}; {r.get('why', '')}",
                    REACH.get(r["widest"], r["widest"]), REACH_PILL.get(r["widest"], "")))
    local = sum(1 for r in rows if r.get("widest") == "local")
    if local:
        out.append((f"{local} more on this computer only", "Nothing else can reach these.", "fine", "ok"))
    return out


def other_machines(st: dict[str, Any]) -> list[str]:
    maps = (st.get("exposure") or {}).get("maps") or {}
    return [k for k in maps if k != st.get("machine")]


def recent_changes(st: dict[str, Any], n: int = 5, now: Optional[float] = None) -> list[str]:
    out = []
    for c in reversed(((st.get("exposure") or {}).get("changes") or [])[-n:]):
        for ch in c.get("changes") or []:
            r = ch.get("row") or {}
            what = "new" if ch.get("kind") == "new" else "now wider"
            out.append(f"{ago(c.get('at'), now)} on {c.get('machine')}: {r.get('process') or 'a program'} port "
                       f"{r.get('port')} ({what}, {REACH.get(r.get('widest'), '')})")
    return out[:n]


def health_line(st: dict[str, Any]) -> tuple[str, str, str]:
    """(title, subtitle, pill kind) for this computer's last check-up."""
    h = (st.get("health") or {}).get(st.get("machine", "")) or {}
    if not h:
        return ("Not checked yet", "Orion checks every Sunday, or press Check now.", "")
    kind = "ok" if h.get("score", 0) >= 75 else "warn"
    return (f"{h.get('score')}/100 ({h.get('word', '')})", f"Checked {ago(h.get('at'))}", kind)


def health_fixes(st: dict[str, Any]) -> list[str]:
    return list(((st.get("health") or {}).get(st.get("machine", "")) or {}).get("fixes") or [])


def supply_line(st: dict[str, Any]) -> tuple[str, str, str]:
    s = st.get("supply") or {}
    if not s.get("at"):
        return ("Not checked yet", "Orion checks your projects every day.", "")
    if not s.get("tools"):
        return ("No checking tools installed", "OrionOS installs osv-scanner; or install cargo-audit / pip-audit.",
                "warn")
    n, serious = s.get("count", 0), s.get("serious", 0)
    title = "No known holes" if not n else f"{n} known hole{'s' if n != 1 else ''} ({serious} serious)"
    return (title, f"Checked {ago(s.get('at'))} with {', '.join(s['tools'])}", "warn" if serious else "ok")


def image_line(st: dict[str, Any]) -> tuple[str, str, str]:
    img = (st.get("supply") or {}).get("image") or {}
    safe = img.get("safe")
    first = str(img.get("answer") or "").split("\n")[0]
    return ({True: "Safe to ship", False: "Not yet", None: "Not scanned"}[safe], first,
            {True: "ok", False: "warn", None: ""}[safe])


def deps_rows(st: dict[str, Any], now: Optional[float] = None) -> list[tuple[str, str, str]]:
    d = st.get("deps") or {}
    runs = {r.get("project"): r for r in d.get("runs") or []}
    out = []
    for p in d.get("projects") or []:
        r = runs.get(p)
        name = p.rstrip("/").rsplit("/", 1)[-1]
        if d.get("running") == name:
            out.append((name, "Testing updates now…", ""))
        elif not r:
            out.append((name, "Not checked yet", ""))
        else:
            first = str(r.get("text") or "").split("\n")[0]
            kind = {"ready": "ok", "up_to_date": "ok"}.get(r.get("state", ""), "warn")
            out.append((name, f"{ago(r.get('at'), now)}: {first}", kind))
    return out


def save(r: Any, section: str, key: str, value: Any) -> tuple[bool, str]:
    res = r.http("POST", f"{API}/security/config", {section: {key: value}})
    if not isinstance(res, dict) or not res.get("ok"):
        err = res.get("error") if isinstance(res, dict) else ""
        return False, err or "Couldn't save it. Is Orion running?"
    return True, "Saved."


def projects_from_text(text: str) -> list[str]:
    return [p.strip() for p in (text or "").replace("\n", ",").split(",") if p.strip()]


def act(r: Any, what: str) -> tuple[bool, str]:
    """The 'now' buttons."""
    routes = {"exposure": ("/exposure/scan", "Checked what's open."),
              "health": ("/host-health/check", "Check-up done."),
              "supply": ("/supply/scan", "Checked your projects for known holes."),
              "deps": ("/deps/run", "Testing updates; the result arrives as a card."),
              "lan": ("/exposure/lan-scan", "Home network scanned.")}
    path, ok_text = routes[what]
    res = r.http("POST", f"{API}{path}", {}, timeout=1800 if what in ("supply", "lan", "health") else 60)
    if not isinstance(res, dict) or res.get("error"):
        return False, (res or {}).get("error") if isinstance(res, dict) else "Orion isn't answering."
    return True, ok_text
