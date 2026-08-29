"""Hybrid retrieval — dense + lexical fused by RRF, with honest degradation (§15-17, §138).

Multi-stage funnel: retrieve(k=50) → rerank(→20) → evidence selection(→10) →
synthesis(→5-8). Each leg is independently fallible:

- embedding/vector unavailable  → lexical-only (degraded flag set)
- BM25 empty                    → dense-only
- both empty                    → RETRIEVAL_MISS surfaced to telemetry

Vector similarity is never treated as truth (§140) — it is one ranked list
among several, fused by rank (RRF), then reranked with authority/freshness.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, Protocol

from atlas.infra.logging import get_logger
from atlas.knowledge.bm25 import BM25Index
from atlas.knowledge.domain import FabricChunk, KnowledgeDocument
from atlas.knowledge.store import FabricStore

_log = get_logger("atlas.knowledge.retrieval")

RRF_K = 60


class Embedder(Protocol):
    async def embed(self, text: str) -> list[float]: ...


class VectorSearcher(Protocol):
    async def search_knowledge(self, query_embedding: list[float], k: int) -> list[Any]: ...


@dataclass(frozen=True)
class Candidate:
    """A chunk that survived retrieval, with its document context."""

    chunk: FabricChunk
    document: KnowledgeDocument
    rrf_score: float
    dense_rank: int | None = None
    lexical_rank: int | None = None
    detail: dict[str, Any] = field(default_factory=dict)


@dataclass
class RetrievalResult:
    candidates: list[Candidate]
    degraded: bool = False
    degradation_reason: str = ""


class HybridRetriever:
    """BM25 + dense, fused by reciprocal-rank fusion."""

    def __init__(
        self,
        store: FabricStore,
        bm25: BM25Index,
        embedder: Embedder | None,
        vector: VectorSearcher | None,
    ) -> None:
        self._store = store
        self._bm25 = bm25
        self._embedder = embedder
        self._vector = vector
        self._by_chunk: dict[str, tuple[FabricChunk, KnowledgeDocument]] = {}
        self._built = False

    async def rebuild(self) -> None:
        """Rebuild the lexical index + chunk cache from SQL (startup, reindex)."""
        corpus = await self._store.all_chunks()
        self._by_chunk = {c.chunk_id: (c, d) for c, d in corpus}
        self._bm25.build([(c.chunk_id, f"{c.heading}\n{c.content}") for c, _ in corpus])
        self._built = True
        _log.info("fabric.bm25_built", event_type="knowledge", chunks=self._bm25.size)

    def index_chunk(self, chunk: FabricChunk, document: KnowledgeDocument) -> None:
        self._by_chunk[chunk.chunk_id] = (chunk, document)
        self._bm25.add(chunk.chunk_id, f"{chunk.heading}\n{chunk.content}")

    def drop_document(self, document_id: str) -> None:
        gone = [cid for cid, (c, _) in self._by_chunk.items() if c.document_id == document_id]
        for cid in gone:
            self._by_chunk.pop(cid, None)
            self._bm25.remove(cid)

    def drop_chunk(self, chunk_id: str) -> None:
        """Forget a single chunk from the lexical index + cache (§12 granular delete)."""
        if self._by_chunk.pop(chunk_id, None) is not None:
            self._bm25.remove(chunk_id)

    def drop_all(self) -> None:
        """Forget the entire in-memory corpus; a later retrieve lazily rebuilds from
        SQL, so after a full purge the rebuilt corpus is empty too (§12)."""
        self._by_chunk.clear()
        self._bm25 = BM25Index()
        self._built = False

    async def retrieve(
        self, query: str, *, k: int = 50, source_types: tuple[str, ...] | None = None
    ) -> RetrievalResult:
        degraded = False
        reason = ""

        if not self._built:  # lazy: DB may not be connected at construction time
            try:
                await self.rebuild()
            except Exception as exc:
                degraded = True
                reason = f"corpus rebuild failed: {exc!r}"

        lexical = self._bm25.query(query, k=k)

        dense_hits: list[Any] = []
        if self._embedder is not None and self._vector is not None:
            try:
                q_emb = await self._embedder.embed(query)
                dense_hits = await self._vector.search_knowledge(q_emb, k=k)
            except Exception as exc:
                degraded = True
                reason = reason or f"vector leg unavailable: {exc!r}"
                _log.warning("fabric.dense_failed", event_type="knowledge", error=repr(exc))
        else:
            degraded = True
            reason = reason or "no embedder/vector configured — lexical only"

        # Map dense hits (embedding_id 'kc_{chunk_id}' or fabric chunk ids) to chunk ids.
        dense_refs: list[str] = []
        for h in dense_hits:
            ref = getattr(h, "ref", "")
            dense_refs.append(ref[3:] if ref.startswith("kc_") else ref)

        if not lexical and not dense_refs:
            return RetrievalResult(candidates=[], degraded=degraded, degradation_reason=reason)
        if not dense_refs and lexical and not degraded:
            degraded = True
            reason = "dense leg returned nothing — lexical only"

        # RRF over the two ranked lists.
        scores: dict[str, float] = {}
        dense_rank: dict[str, int] = {}
        lex_rank: dict[str, int] = {}
        for rank, ref in enumerate(dense_refs):
            if ref in self._by_chunk:
                dense_rank[ref] = rank
                scores[ref] = scores.get(ref, 0.0) + 1.0 / (RRF_K + rank)
        for rank, (ref, _score) in enumerate(lexical):
            if ref in self._by_chunk:
                lex_rank[ref] = rank
                scores[ref] = scores.get(ref, 0.0) + 1.0 / (RRF_K + rank)

        candidates: list[Candidate] = []
        for ref, score in sorted(scores.items(), key=lambda kv: kv[1], reverse=True):
            chunk, doc = self._by_chunk[ref]
            if source_types and doc.source_type.value not in source_types:
                continue
            if doc.security_status.value == "BLOCKED":
                continue
            candidates.append(
                Candidate(
                    chunk=chunk,
                    document=doc,
                    rrf_score=score,
                    dense_rank=dense_rank.get(ref),
                    lexical_rank=lex_rank.get(ref),
                )
            )
            if len(candidates) >= k:
                break

        return RetrievalResult(candidates=candidates, degraded=degraded, degradation_reason=reason)

    async def retrieve_all(
        self, queries: list[str], *, k: int = 50, source_types: tuple[str, ...] | None = None
    ) -> RetrievalResult:
        """Parallel retrieval for rewritten query variants (§14), fused by RRF."""
        if len(queries) == 1:
            return await self.retrieve(queries[0], k=k, source_types=source_types)
        results = await asyncio.gather(*(self.retrieve(q, k=k, source_types=source_types) for q in queries))
        scores: dict[str, float] = {}
        best: dict[str, Candidate] = {}
        degraded = False
        reason = ""
        for res in results:
            degraded = degraded or res.degraded
            reason = reason or res.degradation_reason
            for rank, cand in enumerate(res.candidates):
                ref = cand.chunk.chunk_id
                scores[ref] = scores.get(ref, 0.0) + 1.0 / (RRF_K + rank)
                if ref not in best:
                    best[ref] = cand
        merged = [
            Candidate(
                chunk=best[ref].chunk,
                document=best[ref].document,
                rrf_score=score,
                dense_rank=best[ref].dense_rank,
                lexical_rank=best[ref].lexical_rank,
            )
            for ref, score in sorted(scores.items(), key=lambda kv: kv[1], reverse=True)[:k]
        ]
        return RetrievalResult(candidates=merged, degraded=degraded, degradation_reason=reason)
