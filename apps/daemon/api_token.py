"""The daemon's API key: a random token made fresh at every start (review item B1).

Every HTTP route and the WebSocket on 127.0.0.1:9120 need it (security.py / main.py), except GET /health (which then
only says {"ok": true}) and the tripwire alarm. Programs that may drive Orion read it from a file only the user can
read:

    Linux / macOS   $XDG_RUNTIME_DIR/orion/daemon.token   (a private RAM folder; falls back to the data folder)
    Windows         <data folder>\\daemon.token            (%USERPROFILE%\\.orion, inside the user's own profile)
    anywhere        $ORION_TOKEN_FILE when set

and send it as `Authorization: Bearer <token>` (or `?token=<token>` on the WebSocket handshake, since browsers
can't set headers there). The HUD gets it through a Tauri command; the browser extension pairs once with a short
code instead (extension_pairing.py); the phone app on 9121 signs its requests and doesn't use this at all.
"""
from __future__ import annotations

import hmac
import os
import secrets
import sys
from pathlib import Path
from typing import Optional

FILE_NAME = "daemon.token"
_token: Optional[str] = None
_written: Optional[Path] = None


def _data_dir() -> Path:
    try:
        from config import settings
        return Path(settings.data_dir)
    except Exception:
        return Path(os.path.expanduser(os.getenv("ORION_DATA_DIR", "~/.orion")))


def _runtime_dir() -> Optional[Path]:
    if sys.platform == "win32":
        return None
    rt = os.environ.get("XDG_RUNTIME_DIR", "").strip()
    if rt and os.path.isdir(rt):
        return Path(rt) / "orion"
    return None


def token_path() -> Path:
    """Where this daemon writes its token."""
    explicit = os.environ.get("ORION_TOKEN_FILE", "").strip()
    if explicit:
        return Path(os.path.expanduser(explicit))
    rt = _runtime_dir()
    return (rt / FILE_NAME) if rt else (_data_dir() / FILE_NAME)


def candidate_paths() -> list[Path]:
    """Where a client on this machine looks for the token, in order (same order as the shell/Node/Rust readers)."""
    out: list[Path] = []
    explicit = os.environ.get("ORION_TOKEN_FILE", "").strip()
    if explicit:
        out.append(Path(os.path.expanduser(explicit)))
    rt = _runtime_dir()
    if rt:
        out.append(rt / FILE_NAME)
    elif sys.platform != "win32" and hasattr(os, "getuid"):
        out.append(Path(f"/run/user/{os.getuid()}") / "orion" / FILE_NAME)
    out.append(_data_dir() / FILE_NAME)
    return out


def get() -> str:
    """This run's token (made on first use)."""
    global _token
    if _token is None:
        _token = secrets.token_urlsafe(32)
    return _token


def check(candidate: Optional[str]) -> bool:
    if not candidate:
        return False
    return hmac.compare_digest(candidate.strip().encode(), get().encode())


def from_headers(authorization: Optional[str]) -> str:
    """The token in an `Authorization: Bearer ...` header, or ''."""
    if not authorization:
        return ""
    scheme, _, value = authorization.strip().partition(" ")
    return value.strip() if scheme.lower() == "bearer" else ""


def write() -> Path:
    """Write the token for local clients: owner-only file, folder 0700, replaced atomically."""
    global _written
    path = token_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    if sys.platform != "win32":
        try:
            os.chmod(path.parent, 0o700)
        except OSError:
            pass
    tmp = path.with_name(f".{FILE_NAME}.{os.getpid()}.tmp")
    try:
        tmp.unlink()
    except FileNotFoundError:
        pass
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(get() + "\n")
    os.replace(tmp, path)
    _written = path
    # A token left at another place by an earlier run (crash, different environment) would only cause 401s.
    for other in candidate_paths():
        if other != path:
            _remove_if_stale(other)
    return path


def _remove_if_stale(path: Path) -> None:
    try:
        if path.is_file() and path.read_text(encoding="utf-8").strip() != get():
            path.unlink()
    except OSError:
        pass


def remove() -> None:
    """Delete the token file at shutdown (only if it is still ours)."""
    global _written
    path = _written or token_path()
    try:
        if path.is_file() and path.read_text(encoding="utf-8").strip() == get():
            path.unlink()
    except OSError:
        pass
    _written = None


def read_local() -> str:
    """For clients written in Python (the MCP server, tests): the running daemon's token, or ''."""
    for p in candidate_paths():
        try:
            t = p.read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if t:
            return t
    return ""
