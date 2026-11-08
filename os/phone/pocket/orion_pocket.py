#!/usr/bin/env python3
"""Pocket Orion: a small Orion that runs inside Android 16's Linux terminal (the Debian VM on the phone),
so voice, Video Scout, your vault and a tiny local model still work when the PC is off.

It serves the same signed phone routes as the PC (the app talks to it at http://127.0.0.1:9121 through the
Terminal app's port forwarding):

  /phone/pair, /phone/home, /phone/draft, /phone/chat, /phone/voice, /phone/feed, /phone/scout, /pocket/hello

What it never does: approvals, money, sending messages, agents, missions, the hot mic or anything on the PC.
Those stay on the PC (and approvals still need your fingerprint there): the routes for them answer 403 here
with a plain sentence (PC_ONLY), even though the shared boardroom code would otherwise serve them.

The app (PocketFallback.kt) only sends chat, voice and Scout here, and only when the PC can't be reached; it
checks GET /pocket/hello first (unsigned, answers {"orion": "pocket", ...}).

  orion-pocket serve            run it (the setup script makes a service that does this)
  orion-pocket pair             open pairing; then pair from the Orion app with the code it prints
  orion-pocket confirm <fp>     confirm the phone after comparing fingerprints
  orion-pocket phones           list paired phones
  orion-pocket status           what is installed
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import sys
import tempfile
import types
from pathlib import Path
from typing import Any, Optional

HOME = Path(os.environ.get("ORION_POCKET_HOME", Path.home() / "orion-pocket"))
CODE = HOME / "daemon"             # the copied subset of apps/daemon
DATA = HOME / "data"
PORT = int(os.environ.get("ORION_POCKET_PORT", "9121"))
BIND = os.environ.get("ORION_POCKET_BIND", "127.0.0.1")
OLLAMA_URL = os.environ.get("ORION_POCKET_OLLAMA", "http://127.0.0.1:11434")
OLLAMA_MODEL = os.environ.get("ORION_POCKET_MODEL", "qwen2.5:0.5b")
WHISPER_MODEL = os.environ.get("ORION_POCKET_WHISPER", "tiny.en")
VAULT_HITS = 3
# Routes Pocket Orion refuses outright: approvals, PC actions, the hot mic, agents and Mission Control.
PC_ONLY = ("/phone/verdict", "/phone/pending", "/phone/card/do", "/phone/freeze", "/phone/unfreeze",
           "/phone/hotmic", "/phone/agent", "/phone/mission", "/phone/inbox", "/boardroom/")
PC_ONLY_WORDS = "Pocket Orion can't do that. Approvals, agents and missions only happen on your PC."
VOICE_MISSING = ("Voice isn't installed in Pocket Orion. In the phone's Terminal app run "
                 "orion-pocket-setup.sh again (it needs about 2.5 GB of memory for Linux).")
_HAND_TO_PC = re.compile(r"^\s*(?:hey\s+)?(?:orion[\s,:]+)?(?:please\s+)?(?:(?:have|get|ask|tell)\s+(?:the|my)\s+"
                         r"(?:pc|computer|lab)\b|send\s+(?:this\s+)?(?:work\s+)?to\s+(?:the|my)\s+(?:pc|computer|lab)\b)",
                         re.I)

log = logging.getLogger("orion.pocket")


def _setup_path() -> None:
    sys.path.insert(0, str(CODE))
    DATA.mkdir(parents=True, exist_ok=True)
    if "config" not in sys.modules:   # the full daemon's config reads .env files; the pocket needs three values
        cfg = types.ModuleType("config")
        cfg.settings = types.SimpleNamespace(data_dir=DATA, discord_game_name="", alignment_examples=3)  # type: ignore[attr-defined]
        cfg.PACKAGED = False  # type: ignore[attr-defined]
        sys.modules["config"] = cfg


# ===================================================================================== local model
class PocketModel:
    """Only the phone's own local model. No cloud keys live in the pocket."""

    class cfg:
        ollama_url = OLLAMA_URL
        ollama_model = OLLAMA_MODEL

    def __init__(self) -> None:
        import httpx
        self.http = httpx.AsyncClient()

    async def ollama_available(self) -> bool:
        try:
            r = await self.http.get(f"{OLLAMA_URL}/api/tags", timeout=1.5)
            return r.status_code == 200
        except Exception:
            return False

    async def frontier_text(self, system: str, user: str, **kw: Any) -> tuple[str, str]:
        if not await self.ollama_available():
            raise RuntimeError("no local model on the phone")
        r = await self.http.post(f"{OLLAMA_URL}/api/generate", timeout=180.0, json={
            "model": OLLAMA_MODEL, "system": system, "prompt": user, "stream": False,
            "options": {"temperature": kw.get("temperature", 0.4), "num_predict": kw.get("max_tokens", 400)}})
        r.raise_for_status()
        return str(r.json().get("response") or ""), OLLAMA_MODEL


# ===================================================================================== the vault (keyword search)
def vault_search(query: str, root: Path = DATA / "vault", hits: int = VAULT_HITS) -> list[str]:
    """No embeddings on the phone: score paragraphs of .md/.txt files by shared words."""
    words = {w for w in re.findall(r"[a-z0-9]{3,}", query.lower())}
    if not words or not root.is_dir():
        return []
    scored: list[tuple[int, str]] = []
    for p in sorted(root.rglob("*")):
        if not p.is_file() or p.suffix.lower() not in (".md", ".txt", ".markdown") or p.stat().st_size > 2_000_000:
            continue
        for para in p.read_text(encoding="utf-8", errors="replace").split("\n\n"):
            score = len(words & set(re.findall(r"[a-z0-9]{3,}", para.lower())))
            if score:
                scored.append((score, f"[{p.name}] {para.strip()[:600]}"))
    scored.sort(key=lambda x: -x[0])
    return [t for _, t in scored[:hits]]


# ===================================================================================== the hub phone_api expects
class _NoCards:
    def open_cards(self) -> list:
        return []

    def image(self, cid: str) -> None:
        raise KeyError(cid)

    def done(self, cid: str) -> None:
        raise KeyError(cid)

    def do(self, cid: str) -> None:
        raise KeyError(cid)


class _NoQuick:
    def commands(self) -> dict:
        return {}


class PocketHub:
    def __init__(self) -> None:
        import alerts as alerts_mod
        self.alerts = alerts_mod.AlertCenter(DATA)
        alerts_mod.install(self.alerts)
        self.cards = _NoCards()
        self.quick = _NoQuick()
        self.model = PocketModel()
        self.drafter = None
        try:
            from alignment_drafter import ResponseDrafter
            self.drafter = ResponseDrafter(self.model, None)
        except Exception as exc:
            log.info("Drafts will be plain replies: %s", exc)
        self.scout = None
        self._whisper: Any = None
        self.voice_missing = VOICE_MISSING   # phone_api.py shows this instead of the PC's setup hint

    async def converse(self, text: str, source: str = "phone") -> str:
        if _HAND_TO_PC.match(text or ""):
            return ("Your PC is off, so I can't hand it work right now. Send it again from the Missions screen "
                    "when the PC is on.")
        if self.scout is not None:
            scouted = await self.scout.chat(text, source)
            if scouted is not None:
                return scouted
        notes = vault_search(text)
        system = ("You are Pocket Orion, a small assistant running on the user's phone while their PC is off. "
                  "Answer briefly in plain words. You can't approve, buy, send or change anything.")
        if notes:
            system += "\nNotes from the user's vault that may help:\n" + "\n".join(notes)
        try:
            reply, _ = await self.model.frontier_text(system, text[:2000], max_tokens=300)
            if reply.strip():
                return reply.strip()
        except Exception:
            pass
        if notes:
            return "From your vault:\n" + "\n\n".join(notes)
        return "Pocket Orion is on, but there's no local model on this phone and your PC is off. Try again later."

    async def transcribe(self, audio: bytes) -> Optional[str]:
        try:
            from faster_whisper import WhisperModel
        except ImportError:
            return None

        def run() -> str:
            if self._whisper is None:
                self._whisper = WhisperModel(WHISPER_MODEL, device="cpu", compute_type="int8")
            with tempfile.NamedTemporaryFile(suffix=".m4a") as f:
                f.write(audio)
                f.flush()
                segs, _ = self._whisper.transcribe(f.name, beam_size=1, vad_filter=True)
                return " ".join(s.text.strip() for s in segs).strip()

        try:
            return await asyncio.to_thread(run)
        except Exception as exc:
            log.warning("Voice failed: %s", exc)
            return ""

    async def speak(self, text: str) -> Optional[bytes]:
        return None   # the phone reads replies with its own voice


# ===================================================================================== app
def build_app():
    _setup_path()
    sys.modules["phone_missions"] = None  # type: ignore[assignment]  # never Mission Control in the pocket
    import boardroom
    import phone_api
    import phone_pair

    board = boardroom.Boardroom(DATA)
    hub = PocketHub()
    board.remote_hooks.append(phone_api.register(hub))
    hub.board = board  # type: ignore[attr-defined]
    phone_pair.attach(hub, DATA, name="Pocket Orion")   # /phone/home and /phone/draft (inbox stays off)
    try:
        import scout_routes
        import video_scout as vs

        async def llm(prompt: str, schema: dict[str, Any]) -> dict[str, Any]:
            text, _ = await hub.model.frontier_text("You summarise videos. Reply with one JSON object only.", prompt)
            m = re.search(r"\{.*\}", text, re.S)
            return json.loads(m.group(0) if m else text)

        scout = vs.VideoScout(DATA, llm=llm, vault_dir=DATA / "vault")
        hub.scout = scout_routes.ScoutHub(scout, None)
        board.remote_hooks.append(hub.scout.phone_hook)
    except Exception as exc:
        log.info("Video Scout is not installed in the pocket: %s", exc)

    app = boardroom.build_remote_app(board)

    @app.middleware("http")
    async def pc_only(request, call_next):
        path = request.url.path
        if any(path == p or path.startswith(p if p.endswith("/") else p + "/") for p in PC_ONLY):
            from fastapi.responses import JSONResponse
            return JSONResponse({"error": PC_ONLY_WORDS, "pocket": True}, status_code=403)
        return await call_next(request)

    @app.get("/pocket/hello")
    async def hello() -> dict[str, Any]:
        return {"orion": "pocket", "paired": bool(board.phones.list()), "scout": hub.scout is not None}

    app.state.pocket_hub = hub
    return app, board


def main(argv: list[str]) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    cmd = argv[1] if len(argv) > 1 else "serve"
    if cmd == "serve":
        import uvicorn
        app, _ = build_app()
        server = uvicorn.Server(uvicorn.Config(app, host=BIND, port=PORT, log_level="warning"))

        async def serve() -> None:
            hub = app.state.pocket_hub
            if hub.scout is not None:
                hub.scout.start()        # Video Scout's workers need the running loop
            await server.serve()

        asyncio.run(serve())
        return 0
    _setup_path()
    import boardroom
    board = boardroom.Boardroom(DATA)
    if cmd == "pair":
        print("Pairing code (10 minutes): " + board.phones.new_code())
        print("In the Orion app: Phone setup, the Pocket Orion card. Type this code there.")
        print("Then run: orion-pocket confirm <the fingerprint the app shows>")
        return 0
    if cmd == "confirm":
        try:
            print("Paired: " + json.dumps(board.phones.confirm(argv[2] if len(argv) > 2 else "")))
        except PermissionError as exc:
            print(exc)
            return 1
        return 0
    if cmd == "phones":
        for p in board.phones.list():
            print(f"{p['device_id']}  {p['name']:<20} {p['fingerprint']}")
        return 0
    if cmd == "status":
        for mod, what in (("faster_whisper", "voice"), ("yt_dlp", "Video Scout downloads"), ("httpx", "local model link")):
            try:
                __import__(mod)
                print(f"{what}: installed")
            except Exception:
                print(f"{what}: not installed")
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
