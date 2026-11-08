"""Share the Orion Mobile app on your home Wi-Fi for a few minutes, so the phone can download it by scanning a QR code.

"Start Here.cmd" (os/windows/Start-Here.ps1) and the HUD's Phone panel use it when no phone is paired yet: build the
app once, press share, scan, install. No cable, no Google Drive, no email.

Local routes (127.0.0.1:9120, need the daemon's per-start key like every HUD route):

  GET  /phones/app-share          {"open","until","apk","url","qr_svg","qr_rows","downloads","lan"}
  POST /phones/app-share          {"minutes"?: 1..60, default 15}: open the share window (turns the phone port on)
  POST /phones/app-share/close    close it now

Phone port (9121, the home Wi-Fi):

  GET  /phone/app.apk             the newest built Orion Mobile APK, only while the share window is open.
                                  No key needed: it's the public app, with no keys or personal data inside.
                                  Nothing else is ever served from here: no paths, no parameters, no folder listing.

The window closes by itself after the time is up (and when Orion restarts: it's only kept in memory).
"""
from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path
from typing import Any, Callable, Optional

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, JSONResponse

import qr_svg

log = logging.getLogger("orion.phone_app_share")

REPO = Path(__file__).resolve().parents[2]
APK_TYPE = "application/vnd.android.package-archive"
APK_NAME = "Orion-Mobile.apk"
DEFAULT_MIN = 15
MAX_MIN = 60
ROUTE = "/phone/app.apk"


def find_apk(repo: Path = REPO) -> Optional[Path]:
    """The newest built APK: Orion-Mobile.apk next to Start Here.cmd, or Gradle's output folder."""
    repo = repo.resolve()
    found: list[Path] = []
    top = repo / APK_NAME
    if top.is_file():
        found.append(top)
    out = repo / "apps" / "mobile" / "app" / "build" / "outputs" / "apk"
    if out.is_dir():
        found += [p for p in out.rglob("*.apk") if p.is_file()]
    safe = []
    for p in found:
        try:
            rp = p.resolve()
            rp.relative_to(repo)          # never follow a link out of the Orion folder
        except (OSError, ValueError):
            continue
        safe.append(rp)
    if not safe:
        return None
    return max(safe, key=lambda p: p.stat().st_mtime)


def qr_rows(text: str, border: int = 2) -> list[str]:
    """The QR code as rows of "1" (dark) and "0" (light), quiet border included. Plain ASCII on purpose: the
    console wizard draws it with block characters itself (Windows PowerShell 5.1 mangles non-ASCII JSON)."""
    m = qr_svg.matrix(text, "M")
    n = len(m) + border * 2
    blank = "0" * n
    rows = [blank] * border
    for row in m:
        rows.append("0" * border + "".join("1" if v else "0" for v in row) + "0" * border)
    rows += [blank] * border
    return rows


def qr_text(text: str, border: int = 2) -> str:
    """The QR code drawn with half-block characters, two rows per line, for a terminal with a dark background:
    light squares are drawn, dark squares are left empty (so it looks dark-on-light, which cameras read best)."""
    rows = qr_rows(text, border)
    if len(rows) % 2:
        rows.append("0" * len(rows[0]))
    lines = []
    for top, bottom in zip(rows[0::2], rows[1::2]):
        line = []
        for a, b in zip(top, bottom):
            la, lb = a == "0", b == "0"          # light squares get ink
            line.append("█" if la and lb else "▀" if la else "▄" if lb else " ")
        lines.append("".join(line))
    return "\n".join(lines)


class AppShare:
    def __init__(self, daemon: Any, *, repo: Path = REPO, clock: Callable[[], float] = time.time) -> None:
        self.d = daemon
        self.repo = repo
        self.clock = clock
        self.until = 0.0
        self.downloads = 0

    def is_open(self) -> bool:
        if self.until and self.clock() >= self.until:
            self.until = 0.0                    # time's up: closed
        return bool(self.until)

    def _link(self) -> Any:
        return getattr(self.d, "phone_link", None)

    def url(self) -> str:
        link = self._link()
        base = ""
        if link is not None:
            try:
                base = str(link.address().get("url") or "")
            except Exception:
                base = ""
        return f"{base}{ROUTE}" if base else ""

    def status(self, lan: bool = False) -> dict[str, Any]:
        apk = find_apk(self.repo)
        is_open = self.is_open()
        url = self.url() if (is_open and apk) else ""
        out: dict[str, Any] = {"open": is_open, "until": self.until or None, "apk": apk is not None,
                               "apk_time": apk.stat().st_mtime if apk else None,
                               "url": url, "qr_svg": qr_svg.svg(url) if url else "",
                               "qr_rows": qr_rows(url) if url else [], "downloads": self.downloads, "lan": None}
        if lan and is_open and self._link() is not None:
            try:
                out["lan"] = self._link().lan(True)
            except Exception:
                out["lan"] = None
        return out

    async def open(self, minutes: Any = DEFAULT_MIN) -> dict[str, Any]:
        try:
            m = int(minutes)
        except (TypeError, ValueError):
            m = DEFAULT_MIN
        m = max(1, min(MAX_MIN, m))
        if find_apk(self.repo) is None:
            raise FileNotFoundError("The phone app isn't built yet. Build it first (Start Here.cmd does it).")
        link = self._link()
        if link is not None:
            try:
                await link.ensure_port()
            except Exception as exc:
                log.warning("Couldn't turn the phone port on: %s", exc)
        self.until = self.clock() + m * 60
        self.downloads = 0
        log.info("Sharing the phone app on the home Wi-Fi for %d minutes", m)
        return await asyncio.to_thread(self.status, True)

    def close(self) -> dict[str, Any]:
        self.until = 0.0
        return self.status()

    def serve(self) -> Any:
        if not self.is_open():
            return JSONResponse({"error": "The phone app isn't being shared right now. On the PC: Start Here.cmd, "
                                          "or Orion's Phone panel."}, status_code=404)
        apk = find_apk(self.repo)
        if apk is None:
            return JSONResponse({"error": "The phone app isn't built yet."}, status_code=404)
        self.downloads += 1
        return FileResponse(str(apk), media_type=APK_TYPE, filename=APK_NAME,
                            headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})


# ===================================================================================== wiring
_share: Optional[AppShare] = None


def attach(daemon: Any, **kw: Any) -> AppShare:
    """Called from OrionDaemon.__init__: the share window plus GET /phone/app.apk on the phone port."""
    global _share
    share = AppShare(daemon, **kw)
    _share = share

    def hook(app, phone_auth, json_body) -> None:   # the signed helpers aren't used: this one download is public
        @app.api_route(ROUTE, methods=["GET", "HEAD"])
        async def app_apk():
            return share.serve()

    daemon.board.remote_hooks.append(hook)
    return share


def make_router(get_daemon: Callable[[], Any]) -> APIRouter:
    r = APIRouter()

    def share() -> AppShare:
        s = getattr(get_daemon(), "app_share", None) or _share
        if s is None:
            raise RuntimeError("app sharing isn't running")
        return s

    @r.get("/phones/app-share")
    async def share_status():
        return await asyncio.to_thread(share().status, True)

    @r.post("/phones/app-share")
    async def share_open(request: Request):
        try:
            body = await request.json()
        except Exception:
            body = {}
        minutes = body.get("minutes", DEFAULT_MIN) if isinstance(body, dict) else DEFAULT_MIN
        try:
            return await share().open(minutes)
        except FileNotFoundError as exc:
            return JSONResponse({"error": str(exc)}, status_code=404)

    @r.post("/phones/app-share/close")
    async def share_close():
        return share().close()

    return r
