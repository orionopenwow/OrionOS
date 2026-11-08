"""Memory & Evolution Core — persistent LanceDB vector memory at ~/.orion/memory_db.

Embedding backends (all normalised + fitted to 1536 dims):
  openai  text-embedding-3-small (native 1536)
  ollama  e.g. nomic-embed-text (768, zero-padded)
  st      SentenceTransformers (384, zero-padded; optional dependency)
  hash    deterministic feature-hashing of uni/bi-grams — zero dependencies, fully offline
Zero-padding a unit vector preserves cosine similarity, so padded spaces stay valid.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import time
import uuid
from pathlib import Path
from typing import Any, Awaitable, Callable, List, Optional

import httpx
import lancedb
import numpy as np
import pyarrow as pa

from config import settings

log = logging.getLogger("orion.memory")

TABLE_NAME = "context_logs"
ALIGNMENT_TABLE = "user_alignment"
RULES_TABLE = "user_rules"
_RULE_COLUMNS = ["id", "timestamp", "text", "source", "tier"]
ALIGNMENT_DECISIONS = {"approved", "edited", "rejected"}
_ALIGN_COLUMNS = ["id", "timestamp", "platform", "category", "system", "user_prompt", "draft",
                  "final_response", "decision"]
VALID_SOURCES = {"ocr", "browser", "voice", "discord", "user", "consolidated", "telegram", "messenger",
                 "docs", "vault", "preload", "idea", "devlog"}
MAX_CONTENT_CHARS = 8000
_META_COLUMNS = ["id", "timestamp", "source", "content"]


class Embedder:
    def __init__(self, backend: str = settings.embedding_backend, dim: int = settings.embedding_dim) -> None:
        self.dim = dim
        self.backend = self._resolve(backend)
        self._st_model = None
        self._http = httpx.AsyncClient(timeout=30)
        log.info("Embedding backend: %s (%d dims)", self.backend, dim)

    @staticmethod
    def _resolve(backend: str) -> str:
        if backend != "auto":
            return backend
        if settings.openai_api_key:
            return "openai"
        try:
            import sentence_transformers  # noqa: F401
            return "st"
        except ImportError:
            return "hash"

    async def embed(self, text: str) -> list[float]:
        text = text[:MAX_CONTENT_CHARS]
        try:
            if self.backend == "openai":
                vec = await self._openai(text)
            elif self.backend == "ollama":
                vec = await self._ollama(text)
            elif self.backend == "st":
                vec = await asyncio.to_thread(self._st, text)
            else:
                vec = self._hash(text)
        except Exception as exc:
            # Keep the memory rather than dropping it; similarity for this row
            # will be lexical only. Logged so persistent failures are visible.
            log.warning("Embedding via %s failed (%s); using hash embedding", self.backend, exc)
            vec = self._hash(text)
        return self._fit(vec)

    async def _openai(self, text: str) -> np.ndarray:
        r = await self._http.post(
            "https://api.openai.com/v1/embeddings",
            headers={"Authorization": f"Bearer {settings.openai_api_key}"},
            json={"model": settings.openai_embed_model, "input": text},
        )
        r.raise_for_status()
        return np.asarray(r.json()["data"][0]["embedding"], dtype=np.float32)

    async def _ollama(self, text: str) -> np.ndarray:
        r = await self._http.post(f"{settings.ollama_url}/api/embed",
                                  json={"model": settings.ollama_embed_model, "input": text})
        r.raise_for_status()
        return np.asarray(r.json()["embeddings"][0], dtype=np.float32)

    def _st(self, text: str) -> np.ndarray:
        if self._st_model is None:
            from sentence_transformers import SentenceTransformer
            self._st_model = SentenceTransformer(settings.st_model)
        return np.asarray(self._st_model.encode(text, normalize_embeddings=True), dtype=np.float32)

    def _hash(self, text: str) -> np.ndarray:
        vec = np.zeros(self.dim, dtype=np.float32)
        tokens = re.findall(r"[a-z0-9_]{2,}", text.lower())
        grams = tokens + [f"{a} {b}" for a, b in zip(tokens, tokens[1:])]
        for g in grams:
            h = int.from_bytes(hashlib.blake2b(g.encode(), digest_size=8).digest(), "little")
            vec[h % self.dim] += 1.0 if (h >> 63) & 1 else -1.0
        return np.sign(vec) * np.log1p(np.abs(vec))

    def _fit(self, vec: np.ndarray) -> list[float]:
        vec = np.asarray(vec, dtype=np.float32).ravel()
        if vec.size >= self.dim:
            vec = vec[: self.dim]
        else:
            vec = np.pad(vec, (0, self.dim - vec.size))
        norm = float(np.linalg.norm(vec))
        return (vec / norm if norm > 0 else vec).tolist()


class MemoryCore:
    def __init__(self, db_path: str | None = None, embedder: Embedder | None = None) -> None:
        path = settings.memory_db if db_path is None else db_path
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        self.embedder = embedder or Embedder()
        self.dim = self.embedder.dim
        self.schema = pa.schema([
            pa.field("id", pa.string()),
            pa.field("timestamp", pa.float64()),
            pa.field("source", pa.string()),
            pa.field("content", pa.string()),
            pa.field("vector", pa.list_(pa.float32(), self.dim)),
        ])
        self.db = lancedb.connect(str(path))
        self.table = self.db.create_table(TABLE_NAME, schema=self.schema, exist_ok=True)
        existing_dim = self.table.schema.field("vector").type.list_size
        if existing_dim != self.dim:
            raise RuntimeError(f"{TABLE_NAME} has {existing_dim}-dim vectors, expected {self.dim}. "
                               f"Move {path} aside to rebuild.")
        self._write_lock = asyncio.Lock()
        self._last_content: dict[str, str] = {}

        # V2 Speed-1 self-training: every approved / edited response becomes a few-shot example.
        self.alignment_schema = pa.schema([
            pa.field("id", pa.string()),
            pa.field("timestamp", pa.float64()),
            pa.field("platform", pa.string()),
            pa.field("category", pa.string()),
            pa.field("system", pa.string()),
            pa.field("user_prompt", pa.string()),
            pa.field("draft", pa.string()),
            pa.field("final_response", pa.string()),
            pa.field("decision", pa.string()),  # approved | edited | rejected
            pa.field("vector", pa.list_(pa.float32(), self.dim)),
        ])
        self.alignment = self.db.create_table(ALIGNMENT_TABLE, schema=self.alignment_schema, exist_ok=True)
        # Feature Bible D7 Speed 1: explicit preferences/rules ("keep announcements casual").
        # tier 0 = permanent identity rules from preload; tier 1 = learned from conversation.
        self.rules_schema = pa.schema([
            pa.field("id", pa.string()),
            pa.field("timestamp", pa.float64()),
            pa.field("text", pa.string()),
            pa.field("source", pa.string()),
            pa.field("tier", pa.int32()),
            pa.field("vector", pa.list_(pa.float32(), self.dim)),
        ])
        self.rules_table = self.db.create_table(RULES_TABLE, schema=self.rules_schema, exist_ok=True)
        log.info("LanceDB memory ready at %s (%d rows, %d alignment examples)",
                 path, self.table.count_rows(), self.alignment.count_rows())

    @property
    def backend(self) -> str:
        return self.embedder.backend

    async def ingest_memory(self, source: str, content: str) -> Optional[dict[str, Any]]:
        """Embed and persist a snippet. Returns the stored record (without vector), or None if skipped."""
        if source not in VALID_SOURCES:
            raise ValueError(f"invalid source {source!r}")
        content = (content or "").strip()[:MAX_CONTENT_CHARS]
        if not content or self._last_content.get(source) == content:
            return None
        self._last_content[source] = content
        record = {"id": str(uuid.uuid4()), "timestamp": time.time(), "source": source, "content": content}
        vector = await self.embedder.embed(content)
        async with self._write_lock:
            await asyncio.to_thread(self.table.add, [{**record, "vector": vector}])
        return record

    async def ingest_batch(self, items: list[tuple[str, str]]) -> int:
        """Embed and persist a batch of (source, content) pairs."""
        to_add = []
        for source, content in items:
            src = source if source in VALID_SOURCES else "consolidated"
            cnt = (content or "").strip()[:MAX_CONTENT_CHARS]
            if not cnt:
                continue
            rec = {"id": str(uuid.uuid4()), "timestamp": time.time(), "source": src, "content": cnt}
            vec = await self.embedder.embed(cnt)
            to_add.append({**rec, "vector": vec})
        if to_add:
            async with self._write_lock:
                await asyncio.to_thread(self.table.add, to_add)
        return len(to_add)

    async def query_memory(self, query_str: str, top_k: int = 5, source: str | None = None) -> List[dict]:
        """Vector search; returns top matching snippets with cosine score."""
        if not query_str.strip():
            return []
        vector = await self.embedder.embed(query_str)

        def _search() -> list[dict]:
            q = self.table.search(vector).limit(max(1, min(top_k, 50)))
            if source:
                if source not in VALID_SOURCES:
                    raise ValueError(f"invalid source {source!r}")
                q = q.where(f"source = '{source}'", prefilter=True)
            return q.select(_META_COLUMNS).to_list()

        rows = await asyncio.to_thread(_search)
        # Unit vectors: squared L2 = 2 - 2cos  ->  cos = 1 - d/2
        return [
            {**{k: r[k] for k in _META_COLUMNS}, "score": round(1 - float(r.get("_distance", 2.0)) / 2, 4)}
            for r in rows
        ]

    async def recent(self, limit: int = 50, since: float | None = None, source: str | None = None) -> List[dict]:
        def _recent() -> list[dict]:
            tbl = self.table.to_arrow().select(_META_COLUMNS)
            rows = tbl.sort_by([("timestamp", "descending")]).to_pylist()
            if since is not None:
                rows = [r for r in rows if r["timestamp"] >= since]
            if source:
                rows = [r for r in rows if r["source"] == source]
            return rows[:limit]

        return await asyncio.to_thread(_recent)

    def count(self) -> int:
        return self.table.count_rows()

    # ------------------------------------------------ V2 user alignment
    async def record_alignment(
        self,
        *,
        platform: str,
        category: str,
        system: str,
        user_prompt: str,
        draft: str,
        final_response: str,
        decision: str,
    ) -> dict[str, Any]:
        """Store an admin decision on a drafted response. The prompt is embedded so future
        drafts can retrieve the closest past edits as few-shot examples."""
        if decision not in ALIGNMENT_DECISIONS:
            raise ValueError(f"invalid decision {decision!r}")
        record = {
            "id": str(uuid.uuid4()),
            "timestamp": time.time(),
            "platform": platform[:64],
            "category": category[:64],
            "system": system[:MAX_CONTENT_CHARS],
            "user_prompt": user_prompt[:MAX_CONTENT_CHARS],
            "draft": draft[:MAX_CONTENT_CHARS],
            "final_response": final_response[:MAX_CONTENT_CHARS],
            "decision": decision,
        }
        vector = await self.embedder.embed(f"[{category}] {user_prompt}")
        async with self._write_lock:
            await asyncio.to_thread(self.alignment.add, [{**record, "vector": vector}])
        return record

    async def alignment_examples(self, query: str, top_k: int = 5, category: str | None = None) -> List[dict]:
        """Top-k most similar past approved/edited responses (rejections are excluded)."""
        if not query.strip() or self.alignment.count_rows() == 0:
            return []
        vector = await self.embedder.embed(f"[{category}] {query}" if category else query)

        def _search() -> list[dict]:
            return (
                self.alignment.search(vector)
                .where("decision != 'rejected'", prefilter=True)
                .limit(max(1, min(top_k, 20)))
                .select(_ALIGN_COLUMNS)
                .to_list()
            )

        rows = await asyncio.to_thread(_search)
        return [
            {**{k: r[k] for k in _ALIGN_COLUMNS}, "score": round(1 - float(r.get("_distance", 2.0)) / 2, 4)}
            for r in rows
        ]

    def alignment_count(self) -> int:
        return self.alignment.count_rows()

    # ------------------------------------------------ rules (preferences & identity)
    def list_rules(self) -> list[dict[str, Any]]:
        rows = self.rules_table.to_arrow().select(_RULE_COLUMNS).to_pylist()
        return sorted(rows, key=lambda r: (r["tier"], r["timestamp"]))

    async def add_rule(self, text: str, source: str = "learned", tier: int = 1) -> Optional[dict[str, Any]]:
        text = " ".join((text or "").split())[:400]
        if len(text) < 4:
            return None
        if any(r["text"].lower() == text.lower() for r in self.list_rules()):
            return None
        record = {"id": str(uuid.uuid4()), "timestamp": time.time(), "text": text, "source": source[:32], "tier": int(tier)}
        vector = await self.embedder.embed(text)
        async with self._write_lock:
            await asyncio.to_thread(self.rules_table.add, [{**record, "vector": vector}])
        return record

    async def replace_rules(self, rules: list[str], source: str, tier: int = 0) -> int:
        safe = source.replace("'", "")
        async with self._write_lock:
            await asyncio.to_thread(self.rules_table.delete, f"source = '{safe}'")
        added = 0
        for text in rules:
            if await self.add_rule(text, source=source, tier=tier):
                added += 1
        return added

    async def delete_rule(self, rule_id: str) -> None:
        safe = re.sub(r"[^a-f0-9-]", "", rule_id)
        async with self._write_lock:
            await asyncio.to_thread(self.rules_table.delete, f"id = '{safe}'")

    async def rules_for(self, query: str, top_k: int = 5, max_tier0: int = 15) -> list[str]:
        """All Tier-0 identity rules plus the top-k learned preferences most relevant to `query`."""
        rules = self.list_rules()
        tier0 = [r["text"] for r in rules if r["tier"] == 0][:max_tier0]
        learned = [r for r in rules if r["tier"] != 0]
        picked: list[str] = []
        if learned and query.strip():
            vector = await self.embedder.embed(query)

            def _search() -> list[dict]:
                return (self.rules_table.search(vector).where("tier != 0", prefilter=True)
                        .limit(top_k).select(["text"]).to_list())

            try:
                picked = [r["text"] for r in await asyncio.to_thread(_search)]
            except Exception:
                picked = [r["text"] for r in learned[-top_k:]]
        elif learned:
            picked = [r["text"] for r in learned[-top_k:]]
        return tier0 + [p for p in picked if p not in tier0]

    async def export_finetune(self, path: Path | None = None) -> dict[str, Any]:
        """Speed-2: rewrite the OpenAI chat fine-tuning dataset from every approved/edited triple.
        Each line: {"messages": [{system}, {user: user_prompt}, {assistant: final_approved_response}]}."""
        path = path or settings.finetune_path

        def _export() -> dict[str, Any]:
            rows = self.alignment.to_arrow().select(_ALIGN_COLUMNS).to_pylist()
            rows = [r for r in rows if r["decision"] in ("approved", "edited") and r["final_response"].strip()]
            rows.sort(key=lambda r: r["timestamp"])
            seen: set[tuple[str, str]] = set()
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".jsonl.tmp")
            written = 0
            with tmp.open("w", encoding="utf-8") as fh:
                for r in rows:
                    key = (r["user_prompt"].strip(), r["final_response"].strip())
                    if key in seen:
                        continue
                    seen.add(key)
                    fh.write(json.dumps({"messages": [
                        {"role": "system", "content": r["system"]},
                        {"role": "user", "content": r["user_prompt"]},
                        {"role": "assistant", "content": r["final_response"]},
                    ]}, ensure_ascii=False) + "\n")
                    written += 1
            tmp.replace(path)
            return {"path": str(path), "examples": written, "edited": sum(1 for r in rows if r["decision"] == "edited")}

        result = await asyncio.to_thread(_export)
        log.info("Fine-tune dataset exported: %d examples -> %s", result["examples"], result["path"])
        return result

    async def consolidate(self, summarizer: Optional[Callable[[str], Awaitable[str]]] = None) -> dict[str, Any]:
        """Nightly: fold the last 24h of transient logs into a long-term node, purge stale OCR vectors."""
        now = time.time()
        day = await self.recent(limit=500, since=now - 86400)
        transient = [r for r in day if r["source"] in {"ocr", "browser", "discord", "voice", "user", "telegram", "messenger"}]
        summary_record = None
        if transient:
            digest = "\n".join(
                f"[{time.strftime('%H:%M', time.localtime(r['timestamp']))}] ({r['source']}) {r['content'][:300]}"
                for r in reversed(transient)
            )[:24000]
            summary = None
            if summarizer:
                try:
                    summary = await summarizer(digest)
                except Exception:
                    log.exception("Consolidation summarizer failed")
            if not summary:
                by_source: dict[str, int] = {}
                for r in transient:
                    by_source[r["source"]] = by_source.get(r["source"], 0) + 1
                titles = sorted({r["content"].splitlines()[0][:80] for r in transient if r["source"] == "browser"})[:15]
                summary = (f"Daily digest {time.strftime('%Y-%m-%d')}: "
                           + ", ".join(f"{n} {s}" for s, n in by_source.items())
                           + (". Pages: " + "; ".join(titles) if titles else ""))
            summary_record = await self.ingest_memory("consolidated", summary)

        cutoff = now - settings.ocr_retention_days * 86400
        before = self.count()
        async with self._write_lock:
            await asyncio.to_thread(self.table.delete, f"source = 'ocr' AND timestamp < {cutoff}")
            try:
                await asyncio.to_thread(self.table.optimize)
            except Exception:
                log.debug("optimize() unavailable", exc_info=True)
        purged = before - self.count()
        log.info("Consolidation complete: %d transient logs summarised, %d stale OCR rows purged", len(transient), purged)
        return {"summarised": len(transient), "purged": purged, "record": summary_record}


# --------------------------------------------------------------- module API
_core: Optional[MemoryCore] = None


def get_memory_core() -> MemoryCore:
    global _core
    if _core is None:
        _core = MemoryCore()
    return _core


async def ingest_memory(source: str, content: str) -> Optional[dict[str, Any]]:
    return await get_memory_core().ingest_memory(source, content)


async def query_memory(query_str: str, top_k: int = 5) -> List[dict]:
    return await get_memory_core().query_memory(query_str, top_k)
