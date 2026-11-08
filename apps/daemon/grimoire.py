"""The Living Grimoire — script, command & macro vault (Feature Bible D1).

Sources, merged into one searchable book:
  * grimoire_scripts.json (preload + harvester + things you save)  — shell one-liners & macros
  * ~/.orion/grimoire/  — drop any .bat .cmd .ps1 .sh .py .mjs .js .user.js file in; it's indexed
    automatically (its first comment line becomes the description)
  * recorded workflows from the Watch-and-Learn macro recorder (kind = "workflow")
  * snippets saved from Clipboard Alchemy (kind = "snippet")
  * signed browser skills from the Skill Recorder (kind = "skill", skills_recorder.py)

Find things by what they do, not their file name: "run that script that converts GLB models to M2"
matches on voice triggers, words and embeddings. Anything marked requires_approval, or that looks
destructive, goes through the approval queue before it runs.
"""
from __future__ import annotations

import logging
import re
import sys
from pathlib import Path
from typing import Any, Optional

import numpy as np

from runner import is_destructive
from skills_recorder import SkillStore, grimoire_entry

log = logging.getLogger("orion.grimoire")

SCRIPT_EXTS = {".bat": "batch", ".cmd": "batch", ".ps1": "powershell", ".sh": "bash", ".py": "python",
               ".mjs": "node", ".js": "node", ".user.js": "userscript"}
STOP = {"the", "a", "an", "that", "this", "my", "script", "command", "run", "please", "orion", "to", "for",
        "and", "of", "it", "which", "one", "thing", "with", "me"}


def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", text.lower()) if w not in STOP and len(w) > 1}


def _first_comment(path: Path) -> str:
    try:
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines()[:15]:
            line = line.strip()
            m = re.match(r"^(?:#|//|::|REM\b|<#)\s*(?:@description\s+)?(.{6,})", line, re.I)
            if m and not line.startswith(("#!", "# -*-")) and "==UserScript==" not in line:
                return m.group(1).strip(" #>")[:200]
    except OSError:
        pass
    return ""


_DESTRUCTIVE_CACHE: dict[str, tuple[float, bool]] = {}


def _script_destructive(path: Path) -> bool:
    """A vault script is gated when its *contents* contain destructive commands (rd /s, Remove-Item…)."""
    try:
        mtime = path.stat().st_mtime
        cached = _DESTRUCTIVE_CACHE.get(str(path))
        if cached and cached[0] == mtime:
            return cached[1]
        text = path.read_text(encoding="utf-8", errors="replace")[:200_000]
    except OSError:
        return True
    flag = any(is_destructive(line) for line in text.splitlines())
    _DESTRUCTIVE_CACHE[str(path)] = (mtime, flag)
    return flag


def script_command(path: Path) -> tuple[str, str]:
    """(kind, shell command) for a vault script file."""
    name = path.name.lower()
    ext = ".user.js" if name.endswith(".user.js") else path.suffix.lower()
    kind = SCRIPT_EXTS.get(ext, "file")
    q = f'"{path}"'
    if kind == "batch":
        return kind, q
    if kind == "powershell":
        return kind, f"powershell -NoProfile -ExecutionPolicy Bypass -File {q}"
    if kind == "bash":
        return kind, f"bash {q}"
    if kind == "python":
        return kind, f'"{sys.executable}" {q}'
    if kind == "node":
        return kind, f"node {q}"
    return kind, ""


class Grimoire:
    def __init__(self, preload: Any, data_dir: Path, embedder: Any) -> None:
        self.preload = preload
        self.vault = data_dir / "grimoire"
        self.vault.mkdir(parents=True, exist_ok=True)
        self.skills = SkillStore(data_dir)
        self.embedder = embedder
        self._vectors: dict[str, np.ndarray] = {}
        self._vector_text: dict[str, str] = {}

    # ------------------------------------------------------------ entries
    def entries(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for c in self.preload.grimoire:
            cmd = str(c.get("command") or "")
            kind = c.get("kind") or ("internal" if c.get("internal_action") else
                                     "workflow" if c.get("steps") else "shell")
            out.append({
                "id": str(c.get("id")),
                "kind": kind,
                "description": str(c.get("description") or cmd or c.get("id")),
                "voice_triggers": [str(t) for t in c.get("voice_triggers", [])],
                "command": cmd,
                "cwd": c.get("cwd"),
                "steps": c.get("steps"),
                "snippet": c.get("snippet"),
                "language": c.get("language"),
                "internal_action": c.get("internal_action"),
                "requires_approval": bool(c.get("requires_approval")) or is_destructive(cmd),
                "harvested": bool(c.get("harvested")),
                "source": "book",
            })
        out.extend(grimoire_entry(s) for s in self.skills.all())  # recorded browser skills (kind "skill")
        for path in sorted(self.vault.rglob("*")):
            if not path.is_file():
                continue
            kind, command = script_command(path)
            if kind == "file":
                continue
            rel = path.relative_to(self.vault).as_posix()
            out.append({
                "id": "vault:" + rel,
                "kind": kind,
                "description": _first_comment(path) or path.stem.replace("_", " ").replace("-", " "),
                "voice_triggers": [path.stem.replace("_", " ").replace("-", " ")],
                "command": command,
                "cwd": str(path.parent),
                "path": str(path),
                "requires_approval": kind == "userscript" or is_destructive(command) or _script_destructive(path),
                "source": "vault",
            })
        return out

    def get(self, entry_id: str) -> Optional[dict[str, Any]]:
        return next((e for e in self.entries() if e["id"] == entry_id), None)

    # ------------------------------------------------------------ search
    async def _vector(self, entry: dict[str, Any]) -> np.ndarray:
        text = f"{entry['description']} | {' | '.join(entry['voice_triggers'])} | {entry.get('command', '')[:200]}"
        if self._vector_text.get(entry["id"]) != text:
            self._vectors[entry["id"]] = np.asarray(await self.embedder.embed(text), dtype=np.float32)
            self._vector_text[entry["id"]] = text
        return self._vectors[entry["id"]]

    def match_trigger(self, utterance: str) -> Optional[dict[str, Any]]:
        """Exact voice-trigger hit ("clear stuck ports"), tolerant of 'orion,' / 'please' / punctuation."""
        norm = " ".join(re.findall(r"[a-z0-9]+", utterance.lower()))
        norm = re.sub(r"^(hey )?(orion )?(please )?(can you |could you )?", "", norm).strip()
        for e in self.entries():
            for t in e["voice_triggers"]:
                tn = " ".join(re.findall(r"[a-z0-9]+", t.lower()))
                if tn and re.sub(r"\s+please$", "", norm) == tn:
                    return e
        return None

    async def search(self, query: str, top_k: int = 5) -> list[dict[str, Any]]:
        entries = self.entries()
        if not entries or not query.strip():
            return []
        qwords = _words(query)
        qvec = np.asarray(await self.embedder.embed(query), dtype=np.float32)
        scored = []
        for e in entries:
            hay = _words(" ".join([e["description"], *e["voice_triggers"], e.get("command", ""), e["id"]]))
            overlap = len(qwords & hay) / max(1, len(qwords))
            cos = float(np.dot(qvec, await self._vector(e)))
            score = 0.6 * overlap + 0.4 * max(0.0, cos)
            scored.append({**e, "score": round(score, 3)})
        scored.sort(key=lambda e: e["score"], reverse=True)
        return scored[:top_k]

    async def resolve(self, query: str, threshold: float = 0.45) -> Optional[dict[str, Any]]:
        hit = self.match_trigger(query)
        if hit:
            return {**hit, "score": 1.0}
        results = await self.search(query, top_k=2)
        if not results or results[0]["score"] < threshold:
            return None
        if len(results) > 1 and results[0]["score"] - results[1]["score"] < 0.05:
            return None  # ambiguous: let the user pick in the HUD
        return results[0]

    # ------------------------------------------------------------ saving
    def save_snippet(self, text: str, description: str, language: str = "") -> dict[str, Any]:
        entry = {
            "id": re.sub(r"[^a-z0-9]+", "_", description.lower()).strip("_")[:40] or "snippet",
            "kind": "snippet",
            "description": description[:200],
            "voice_triggers": [description.lower()[:60]],
            "snippet": text[:20000],
            "language": language,
            "requires_approval": False,
        }
        return self.preload.upsert_grimoire(entry)

    def save_workflow(self, name: str, steps: list[dict[str, Any]], triggers: list[str], description: str = "") -> dict[str, Any]:
        entry = {
            "id": re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")[:40] or "macro",
            "kind": "workflow",
            "description": description or f"Recorded macro: {name}",
            "voice_triggers": [t for t in triggers if t.strip()][:6] or [name.lower()],
            "steps": steps,
            "requires_approval": False,
        }
        return self.preload.upsert_grimoire(entry)
