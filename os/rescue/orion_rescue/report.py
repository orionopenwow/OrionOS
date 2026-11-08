""""Send a report to my phone": a short summary of what went wrong goes to the phone through Orion's daemon (the
same notice route Get apps uses), and the full report is saved in the home folder. Secrets that may appear in
logs (keys, tokens, passwords) are blanked out before anything is written or sent."""
from __future__ import annotations

import datetime as _dt
import os
import platform
import re
import subprocess
from pathlib import Path
from typing import Callable, Optional

from . import crashes, daemon_api

SECRET = re.compile(
    r"(?i)(\b(?:api[_-]?key|token|secret|password|passwd|bearer|authorization|private[_-]?key)\b\s*[:=]?\s*(?:bearer\s+)?)"
    r"([^\s'\"]{4,})")
LONG_KEY = re.compile(r"\b(?:sk|pk|ghp|gho|xox[abp]|AIza)[-_A-Za-z0-9]{12,}\b")


def scrub(text: str) -> str:
    text = SECRET.sub(lambda m: m.group(1) + "[hidden]", text)
    return LONG_KEY.sub("[hidden]", text)


def _run(argv: list[str], timeout: float = 10) -> str:
    try:
        r = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
        return (r.stdout or r.stderr or "").strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def build(run: Callable[[list[str]], str] = _run) -> str:
    when = _dt.datetime.now().strftime("%Y-%m-%d %H:%M")
    version = ""
    for p in ("/etc/orion/provisioned", "/opt/orion-src/BUNDLE_VERSION"):
        try:
            version = Path(p).read_text(encoding="utf-8").strip()
            break
        except OSError:
            continue
    c = crashes.summary()
    parts = [
        f"OrionOS rescue report, {when}",
        f"System: {platform.platform()}  ({version or 'version unknown'})",
        f"Orion screen crashes just now: {c['recent_crashes']} (rescue panel opened {c['rescues']} time(s))",
        f"Orion's brain answering: {'yes' if daemon_api.alive() else 'no'}",
        "",
        "Stopped services:",
        run(["systemctl", "--user", "--failed", "--no-legend", "--plain"]) or "  none",
        "",
        "Orion screen (last lines):",
        run(["journalctl", "--user", "-u", "orion-hud.service", "-n", "40", "--no-pager", "-o", "cat"]),
        "",
        "Orion's brain (last lines):",
        run(["journalctl", "--user", "-u", "orion-daemon.service", "-n", "40", "--no-pager", "-o", "cat"]),
    ]
    return scrub("\n".join(parts))


def short(report: str) -> str:
    lines = [ln for ln in report.splitlines()[1:5] if ln.strip()]
    return " ".join(lines)[:560]


def save(report: str, home: Optional[Path] = None) -> Path:
    home = home or Path.home()
    d = home / "Orion reports"
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"rescue-{_dt.datetime.now().strftime('%Y%m%d-%H%M%S')}.txt"
    p.write_text(report + "\n", encoding="utf-8")
    os.chmod(p, 0o600)
    return p


def send(report: Optional[str] = None) -> tuple[bool, str]:
    report = report or build()
    path = save(report)
    res = daemon_api.request("POST", "/apps/notify", {"title": "Rescue report from your PC", "body": short(report)})
    if isinstance(res, dict) and res.get("ok"):
        return True, f"Sent to your phone. The full report is in {path.parent.name}/{path.name}."
    return False, ("Orion's brain isn't answering, so your phone can't be reached from here. The report is saved "
                   f"in your home folder: {path.parent.name}/{path.name}.")
