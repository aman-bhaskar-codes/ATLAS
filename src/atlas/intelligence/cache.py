"""Semantic Response Cache — exact + semantic match before hitting a model.

WHY local Protocol definitions: atlas.intelligence must not import atlas.memory
(layer boundary: intelligence < memory in the stack). We define minimal Embedder
and VectorHit/VectorStore protocols here; the real implementations (OllamaEmbedder,
ChromaVectorStore) satisfy them structurally.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Protocol

from atlas.infra.db import Database
from atlas.infra.logging import get_logger
from atlas.intelligence.contracts import InferenceRequest, InferenceResponse

_log = get_logger("atlas.cache")


# ---------------------------------------------------------------------------
# Local protocols — avoids importing atlas.memory (layer boundary violation)
# ---------------------------------------------------------------------------

class _VectorHit:
    """Structural match for atlas.memory.vectorstore.VectorHit."""
    ref: str
    score: float
    text: str


class _Embedder(Protocol):
    async def embed(self, text: str) -> list[float]: ...


class _VectorHitProto(Protocol):
    ref: str
    score: float


class _VectorStore(Protocol):
    async def upsert(self, ref: str, text: str, embedding: list[float]) -> None: ...
    async def query(self, embedding: list[float], k: int) -> list[Any]: ...


# ---------------------------------------------------------------------------
# SemanticCache
# ---------------------------------------------------------------------------

class SemanticCache:
    def __init__(self, db: Database, vectors: _VectorStore, embedder: _Embedder) -> None:
        self._db = db
        self._vectors = vectors
        self._embedder = embedder

    def _hash_prompt(self, req: InferenceRequest) -> str:
        prompt_text = "\n".join(f"{m.role}: {m.content}" for m in req.messages)
        return hashlib.sha256(prompt_text.encode("utf-8")).hexdigest()

    async def get(self, req: InferenceRequest) -> InferenceResponse | None:
        # Avoid caching streaming responses
        if req.stream:
            return None

        prompt_hash = self._hash_prompt(req)
        now_iso = datetime.now(timezone.utc).isoformat()

        # 1. Exact match fast-path
        cur = await self._db.conn.execute(
            "SELECT response_json, ttl_expires_ts FROM semantic_cache WHERE prompt_hash = ?",
            (prompt_hash,),
        )
        row = await cur.fetchone()
        if row:
            if row["ttl_expires_ts"] and row["ttl_expires_ts"] < now_iso:
                pass  # expired — fall through to semantic match
            else:
                _log.info("cache.hit_exact", event_type="cache", prompt_hash=prompt_hash)
                return InferenceResponse.model_validate_json(row["response_json"])

        # 2. Semantic similarity match (threshold 0.97 to avoid false positives)
        prompt_text = "\n".join(f"{m.role}: {m.content}" for m in req.messages)
        embedding = await self._embedder.embed(prompt_text)
        hits = await self._vectors.query(embedding, k=1)

        if hits and hits[0].score > 0.97:
            ref = hits[0].ref
            cur = await self._db.conn.execute(
                "SELECT response_json, ttl_expires_ts FROM semantic_cache WHERE embedding_ref = ?",
                (ref,),
            )
            row = await cur.fetchone()
            if row:
                if row["ttl_expires_ts"] and row["ttl_expires_ts"] < now_iso:
                    pass  # expired
                else:
                    _log.info("cache.hit_semantic", event_type="cache", score=hits[0].score)
                    return InferenceResponse.model_validate_json(row["response_json"])

        return None

    async def put(self, req: InferenceRequest, resp: InferenceResponse) -> None:
        if req.stream:
            return

        # Time-sensitive capabilities get a short TTL
        time_sensitive = {"calendar", "mail", "web", "browser", "search", "contacts"}
        req_caps = set(req.required_capabilities)
        ttl = timedelta(minutes=5) if req_caps & time_sensitive else timedelta(days=1)
        expires_ts = (datetime.now(timezone.utc) + ttl).isoformat()

        prompt_hash = self._hash_prompt(req)
        prompt_text = "\n".join(f"{m.role}: {m.content}" for m in req.messages)

        ref = str(uuid.uuid4())
        embedding = await self._embedder.embed(prompt_text)

        await self._vectors.upsert(ref, prompt_text, embedding)

        resp_json = resp.model_dump_json()
        now_iso = datetime.now(timezone.utc).isoformat()

        await self._db.conn.execute(
            "INSERT INTO semantic_cache "
            "(id, prompt_hash, embedding_ref, response_json, ttl_expires_ts, created_ts) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (str(uuid.uuid4()), prompt_hash, ref, resp_json, expires_ts, now_iso),
        )
        await self._db.conn.commit()
