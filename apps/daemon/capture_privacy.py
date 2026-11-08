"""What screen reading (capture_engine.py) must never look at, and the visible "Orion is reading the screen" sign.

Screen reading is off unless the user turns it on (first-run asks; Settings > Privacy). When it is on, a frame is
skipped entirely (no screenshot kept, no OCR) while any of these is on screen:
  - the lock screen
  - a password manager (1Password, Bitwarden, KeePassXC, ...)
  - a banking or payment site or app
  - a private / incognito browser window
  - any window whose title looks like a login or password prompt (macro_recorder.SENSITIVE_TITLE, the same rule
    the macro recorder uses)
  - any window whose title contains one of your own words (ORION_CAPTURE_EXCLUDE="word, other word", or one per line
    in ~/.config/orion/capture-exclude.txt)
On Sway every visible window counts, not only the focused one.

While reading is on, the marker $XDG_RUNTIME_DIR/orion-capture-on exists; the desktop shows it (os/lab/bin/
orion-statusbar keeps the top bar visible with "Orion is reading the screen").
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Iterable, Optional

try:
    from macro_recorder import SENSITIVE_TITLE
except Exception:  # keep screen reading safe even if that module can't load
    SENSITIVE_TITLE = re.compile(r"(password|sign in|log ?in|1password|bitwarden|keepass|bank|credential)", re.I)

PASSWORD_MANAGERS = re.compile(
    r"(1password|bitwarden|keepass(xc)?|lastpass|dashlane|proton ?pass|enpass|nordpass|keeper|roboform|"
    r"passwords? and keys|seahorse|kwallet|passbolt|vaultwarden|gnome-keyring)", re.I)
BANKING = re.compile(
    r"(\bbank|banking|online banking|credit union|paypal|venmo|revolut|monzo|\bwise\b|stripe dashboard|"
    r"chase|wells fargo|barclays|hsbc|santander|lloyds|natwest|capital one|american express|\bamex\b|"
    r"coinbase|kraken|binance|ledger live|metamask|crypto wallet|brokerage|fidelity|vanguard|schwab|"
    r"tax return|irs\.gov|hmrc)", re.I)
PRIVATE_WINDOW = re.compile(r"(private browsing|incognito|inprivate|private window|\(private\)|tor browser)", re.I)
LOCK_APPS = re.compile(r"^(swaylock|gtklock|waylock|hyprlock)$", re.I)
SENSITIVE_APP_IDS = re.compile(
    r"(1password|bitwarden|keepassxc|org\.keepassxc|com\.bitwarden|lastpass|org\.gnome\.seahorse|"
    r"enpass|protonpass|kwalletmanager|ai\.orion\.Settings.*password)", re.I)

RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("a password manager", PASSWORD_MANAGERS),
    ("a banking or payment page", BANKING),
    ("a private browser window", PRIVATE_WINDOW),
    ("a login or password prompt", SENSITIVE_TITLE),
)


def own_words(environ: Optional[dict[str, str]] = None, home: Optional[Path] = None) -> list[str]:
    env = os.environ if environ is None else environ
    words = [w.strip() for w in env.get("ORION_CAPTURE_EXCLUDE", "").split(",")]
    p = (home or Path.home()) / ".config" / "orion" / "capture-exclude.txt"
    try:
        words += [ln.strip() for ln in p.read_text(encoding="utf-8").splitlines()]
    except OSError:
        pass
    return [w.lower() for w in words if w and not w.startswith("#")][:200]


def why_skip_window(title: str, app_id: str = "", words: Iterable[str] = ()) -> str:
    """'' = fine to read; otherwise a plain reason ("a password manager")."""
    t = title or ""
    if app_id and (SENSITIVE_APP_IDS.search(app_id) or LOCK_APPS.match(app_id)):
        return "a password manager" if not LOCK_APPS.match(app_id) else "the lock screen"
    for reason, rx in RULES:
        if rx.search(t) or (app_id and rx is not SENSITIVE_TITLE and rx.search(app_id)):
            return reason
    low = t.lower()
    for w in words:
        if w in low:
            return "a window on your own list"
    return ""


def runtime_dir() -> Optional[Path]:
    r = os.environ.get("XDG_RUNTIME_DIR")
    return Path(r) if r else None


def locked(proc: Path = Path("/proc")) -> bool:
    rd = runtime_dir()
    if rd is not None and (rd / "orion-locked").exists():
        return True
    try:
        uid = os.getuid()
        for d in proc.iterdir():
            if not d.name.isdigit():
                continue
            try:
                if d.stat().st_uid != uid:
                    continue
                if LOCK_APPS.match((d / "comm").read_text().strip()):
                    return True
            except OSError:
                continue
    except (OSError, AttributeError):
        return False
    return False


def visible_windows() -> list[dict[str, Any]]:
    """Every visible window on Sway (title, app_id); [] elsewhere."""
    try:
        import linux_desktop
        if not linux_desktop.is_sway():
            return []
        tree = linux_desktop.swaymsg("-t", "get_tree", json_out=True)
    except Exception:
        return []
    out: list[dict[str, Any]] = []

    def walk(n: Any) -> None:
        if not isinstance(n, dict):
            return
        if n.get("type") in ("con", "floating_con") and (n.get("pid") or n.get("app_id")) and n.get("visible"):
            props = n.get("window_properties") or {}
            out.append({"title": str(n.get("name") or ""),
                        "app_id": str(n.get("app_id") or props.get("class") or "")})
        for k in ("nodes", "floating_nodes"):
            for c in n.get(k) or []:
                walk(c)
    walk(tree)
    return out


def skip_reason(focused_title: Optional[str], windows: Optional[list[dict[str, Any]]] = None,
                is_locked: Optional[bool] = None, words: Optional[list[str]] = None) -> str:
    """Why this frame must not be read ('' = read it)."""
    if (locked() if is_locked is None else is_locked):
        return "the lock screen"
    words = own_words() if words is None else words
    wins = visible_windows() if windows is None else windows
    for w in wins + [{"title": focused_title or "", "app_id": ""}]:
        why = why_skip_window(w.get("title", ""), w.get("app_id", ""), words)
        if why:
            return why
    return ""


def indicate(on: bool) -> None:
    """Keep the "Orion is reading the screen" marker in step with reality."""
    rd = runtime_dir()
    if rd is None:
        return
    p = rd / "orion-capture-on"
    try:
        if on:
            if not p.exists():
                p.write_text("on\n")
        elif p.exists():
            p.unlink()
    except OSError:
        pass
