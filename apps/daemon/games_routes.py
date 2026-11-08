"""Games, daemon side: what the HUD's Games window shows and does (OrionOS; os/lab/bin/orion-games does the work).

    GET  /games              {ok, windows: [...], here: [...], launchers: {...}, general_note, windows_drive}
                             (on Windows or without orion-games: {ok: false, message})
    POST /games/do {action, id?}
        action = play            starts the game (your click in the HUD; nothing else can be started)
        action = setup-steam | setup-heroic | setup-battlenet | setup-wow112 | setup-extras | import | menu
                                 opens a terminal running orion-games, because these ask you before installing
                                 or copying anything (and Get apps may ask for your password)
Nothing here installs, copies or downloads by itself.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import subprocess
from typing import Any, Callable, Optional

ID_RE = re.compile(r"^(steam|epic|wow|wow112|lutris):[A-Za-z0-9._ -]{1,120}$")
SETUPS = {"setup-steam": "steam", "setup-heroic": "heroic", "setup-battlenet": "battlenet", "setup-wow112": "wow112",
          "setup-extras": "extras"}


def cli() -> Optional[str]:
    env = os.environ.get("ORION_GAMES_CLI")
    if env:
        return env
    for p in ("/usr/local/bin/orion-games", "/opt/orion/bin/orion-games"):
        if os.access(p, os.X_OK):
            return p
    return shutil.which("orion-games")


def _open_terminal(command: list[str]) -> bool:
    for term in ("foot", "x-terminal-emulator"):
        exe = shutil.which(term)
        if not exe:
            continue
        argv = ([exe, "--title=orion-games"] if term == "foot" else [exe, "-e"]) + command
        try:
            subprocess.Popen(argv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
            return True
        except OSError:
            continue
    return False


def list_games(runner: Callable[..., subprocess.CompletedProcess] = subprocess.run) -> dict[str, Any]:
    c = cli()
    if not c:
        return {"ok": False, "message": "Games works on OrionOS (orion-games isn't on this computer)."}
    try:
        r = runner([c, "list", "--json"], capture_output=True, text=True, timeout=60)
        return json.loads(r.stdout)
    except (OSError, ValueError, subprocess.SubprocessError):
        return {"ok": False, "message": "Couldn't look for games just now."}


def do(action: str, gid: str = "", open_terminal: Callable[[list[str]], bool] = _open_terminal,
       popen: Callable[..., Any] = subprocess.Popen) -> dict[str, Any]:
    c = cli()
    if not c:
        return {"ok": False, "message": "Games works on OrionOS."}
    if action == "play":
        if not ID_RE.match(gid or ""):
            return {"ok": False, "message": "That's not a game Orion found."}
        try:
            popen([c, "play", gid], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        except OSError:
            return {"ok": False, "message": "Couldn't start it."}
        return {"ok": True, "message": "Starting it."}
    if action in SETUPS:
        cmd = [c, "setup", SETUPS[action]]
    elif action == "import":
        if not ID_RE.match(gid or ""):
            return {"ok": False, "message": "That's not a game Orion found."}
        cmd = [c, "import", gid]
    elif action == "menu":
        cmd = [c]
    else:
        return {"ok": False, "message": "Orion doesn't know that one."}
    ok = open_terminal(cmd)
    return {"ok": ok, "message": "Opened in a terminal window: it asks before installing or copying anything."
            if ok else "Couldn't open a terminal. Run it yourself: " + " ".join(["orion-games"] + cmd[1:])}


def make_router(get_daemon: Callable[[], Any]):
    from fastapi import APIRouter
    from pydantic import BaseModel

    router = APIRouter()

    class DoBody(BaseModel):
        action: str
        id: str = ""

    @router.get("/games")
    async def games() -> dict[str, Any]:
        return await asyncio.to_thread(list_games)

    @router.post("/games/do")
    async def games_do(body: DoBody) -> dict[str, Any]:
        return await asyncio.to_thread(do, body.action, body.id)

    return router
