"""Offline Knowledge & Field Vault (Feature Bible D6).

Drop markdown, text or PDF guides into ~/.orion/vault/ (foraging IDs, natural-building specs, camp
recipes, hardware pinouts …). Orion embeds them into memory (source "vault") with local embeddings, so
answers work boondocking off-grid through local Ollama just as well as on fiber. Re-scanned every
10 minutes; only new or changed files are re-embedded. PDFs need the optional `pypdf` package.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from pathlib import Path
from typing import Any

from harvester import chunk_text

log = logging.getLogger("orion.vault")

TEXT_EXT = {".md", ".markdown", ".txt", ".rst", ".csv", ".json"}
MAX_BYTES = 5 * 1024 * 1024


def _read(path: Path) -> str:
    if path.suffix.lower() == ".pdf":
        try:
            from pypdf import PdfReader
        except ImportError:
            return ""
        try:
            return "\n\n".join((p.extract_text() or "") for p in PdfReader(str(path)).pages[:300])
        except Exception as exc:
            log.info("Could not read %s: %s", path.name, exc)
            return ""
    return path.read_text(encoding="utf-8", errors="replace")


class KnowledgeVault:
    def __init__(self, data_dir: Path) -> None:
        self.dir = data_dir / "vault"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.state_path = data_dir / "vault_state.json"
        readme = self.dir / "README.txt"
        if not readme.exists():
            readme.write_text("Drop .md / .txt / .pdf guides here. Orion indexes them for offline answers.\n",
                              encoding="utf-8")

    def _state(self) -> dict[str, str]:
        try:
            return json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def files(self) -> list[dict[str, Any]]:
        state = self._state()
        out = []
        for p in sorted(self.dir.rglob("*")):
            if p.is_file() and (p.suffix.lower() in TEXT_EXT or p.suffix.lower() == ".pdf") and p.name != "README.txt":
                out.append({"name": p.relative_to(self.dir).as_posix(), "bytes": p.stat().st_size,
                            "indexed": str(p) in state})
        return out

    async def sync(self, memory: Any) -> dict[str, int]:
        state = self._state()
        files = chunks = 0

        def candidates() -> list[Path]:
            return [p for p in sorted(self.dir.rglob("*"))
                    if p.is_file() and p.name != "README.txt" and p.stat().st_size <= MAX_BYTES
                    and (p.suffix.lower() in TEXT_EXT or p.suffix.lower() == ".pdf")]

        def load(p: Path) -> tuple[str, str]:
            digest = hashlib.sha1(p.read_bytes()).hexdigest()
            return digest, ("" if state.get(str(p)) == digest else _read(p))

        # file IO, hashing and PDF extraction stay off the event loop
        for p in await asyncio.to_thread(candidates):
            digest, text = await asyncio.to_thread(load, p)
            if state.get(str(p)) == digest or not text.strip():
                continue
            rel = p.relative_to(self.dir).as_posix()
            items = [("vault", f"[Vault · {rel}]\n{c}") for c in chunk_text(text)[:400]]
            chunks += await memory.ingest_batch(items)
            state[str(p)] = digest
            files += 1
        if files:
            self.state_path.write_text(json.dumps(state, indent=1), encoding="utf-8")
            log.info("Vault: indexed %d files (%d chunks)", files, chunks)
        return {"files": files, "chunks": chunks}
