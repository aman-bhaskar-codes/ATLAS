"""Ingestion pipeline — the canonical SOURCE→INDEX path (§23-25).

Every source (file, browsed page, search result, memory export) enters here
and walks the same bounded state machine:

DISCOVERED → FETCHING → PARSING → NORMALIZING → CHUNKING → EMBEDDING → INDEXING → READY
                                                                               ↘ FAILED

Rules:
- content-hash dedupe: unchanged content is never re-processed (§24)
- pipeline_version stamps every document; bumping parser/chunker/embedding
  versions marks old documents stale for re-indexing (§25)
- injection scan runs in NORMALIZING; BLOCKED documents never reach an index
- embedding failure never kills ingestion — the lexical leg still indexes
"""

from __future__ import annotations

import asyncio
import urllib.parse
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol

from atlas.infra.clock import Clock
from atlas.infra.ids import IdGenerator
from atlas.infra.logging import get_logger
from atlas.knowledge.chunking import chunk_parsed
from atlas.knowledge.domain import (
    AUTHORITY_FLOOR,
    PIPELINE_VERSION,
    FailureCause,
    IngestionJob,
    IngestionState,
    KnowledgeDocument,
    SecurityStatus,
    SourceType,
    content_hash,
    make_document_id,
)
from atlas.knowledge.injection import scan_for_injection
from atlas.knowledge.parsers import parse_content
from atlas.knowledge.retrieval import HybridRetriever
from atlas.knowledge.store import FabricStore

_log = get_logger("atlas.knowledge.ingestion")

_OFFICIAL_DOMAINS = {
    "github.com",
    "docs.python.org",
    "developer.mozilla.org",
    "en.wikipedia.org",
    "arxiv.org",
    "dl.acm.org",
    "doi.org",
}


class Embedder(Protocol):
    async def embed(self, text: str) -> list[float]: ...


class VectorIndexer(Protocol):
    async def add_knowledge_chunk(
        self, chunk_id: str, text: str, embedding: list[float], metadata: dict[str, Any]
    ) -> str: ...


def domain_authority(uri: str) -> float:
    """Cheap URI-based authority prior (reused shape of the browser SourceRanker)."""
    try:
        domain = urllib.parse.urlparse(uri).netloc.lower()
    except Exception:
        return 0.5
    if not domain:
        return 0.5
    if domain.startswith("www."):
        domain = domain[4:]
    if domain in _OFFICIAL_DOMAINS or any(domain.endswith(f".{d}") for d in _OFFICIAL_DOMAINS):
        return 1.0
    if domain.endswith((".edu", ".gov")):
        return 0.9
    if domain.endswith(".org"):
        return 0.7
    return 0.5


def freshness_score(published_at: datetime | None, retrieved_at: datetime, *, half_life_days: float = 90.0) -> float:
    """Exponential decay: 1.0 when fresh, 0.5 after one half-life."""
    ref = published_at or retrieved_at
    age_days: float = 0.0
    try:
        age_days = max(0.0, (retrieved_at - ref).total_seconds() / 86400.0)
    except TypeError:  # tz mixup — treat as fresh-ish
        return 0.5
    return float(round(0.5 ** (age_days / half_life_days), 3))


class IngestionPipeline:
    def __init__(
        self,
        store: FabricStore,
        retriever: HybridRetriever,
        ids: IdGenerator,
        clock: Clock,
        *,
        embedder: Embedder | None = None,
        vector: VectorIndexer | None = None,
    ) -> None:
        self._store = store
        self._retriever = retriever
        self._ids = ids
        self._clock = clock
        self._embedder = embedder
        self._vector = vector

    async def ingest(
        self,
        *,
        source_id: str,
        source_type: SourceType,
        content: str,
        title: str = "",
        uri: str = "",
        content_type: str = "text/plain",
        author: str = "",
        published_at: datetime | None = None,
        authority: float | None = None,
        trust_score: float | None = None,
        license_: str = "",
        metadata: dict[str, Any] | None = None,
        provenance: dict[str, Any] | None = None,
    ) -> IngestionJob:
        now = self._clock.now()
        job_id = f"job_{self._ids.execution_id()}"
        job = IngestionJob(
            job_id=job_id,
            source=uri or source_id,
            source_type=source_type,
            state=IngestionState.DISCOVERED,
            created_ts=now,
            updated_ts=now,
        )

        if not content.strip():
            return _fail(job, "empty content", FailureCause.PARSER_FAILURE)

        # ── incremental: identical content already indexed? (§24) ────
        h = content_hash(content)
        existing = await self._store.find_document_by_hash(h)
        if existing is not None:
            return job.model_copy(
                update={
                    "state": IngestionState.READY,
                    "document_id": existing.document_id,
                    "updated_ts": self._clock.now(),
                }
            )

        # ── PARSING ──────────────────────────────────────────────────
        job = _move(job, IngestionState.PARSING)
        try:
            parsed = parse_content(content, content_type=content_type, uri=uri, title=title)
        except Exception as exc:
            return _fail(job, f"parser error: {exc!r}", FailureCause.PARSER_FAILURE)
        if not parsed.text.strip():
            return _fail(job, "parser produced no text", FailureCause.PARSER_FAILURE)

        # ── NORMALIZING: canonical document + security verdict ───────
        job = _move(job, IngestionState.NORMALIZING)
        document_id = make_document_id(source_type, uri or source_id, now)
        report = scan_for_injection(parsed.text)
        if report.status is SecurityStatus.BLOCKED:
            _log.warning("fabric.ingest_blocked", event_type="knowledge", uri=uri, flags=list(report.flags))
            return _fail(job, f"blocked: injection severity {report.severity}", FailureCause.BROWSER_EXTRACTION_FAILURE)

        auth = authority if authority is not None else max(domain_authority(uri), AUTHORITY_FLOOR.get(source_type, 0.5))
        doc = KnowledgeDocument(
            document_id=document_id,
            source_id=source_id,
            source_type=source_type,
            title=parsed.title or title or uri or source_id,
            uri=uri,
            canonical_uri=_canonical(uri) or document_id,
            content=parsed.text,
            content_type=content_type,
            author=author,
            published_at=published_at,
            retrieved_at=now,
            content_hash=h,
            authority=round(auth, 3),
            trust_score=round(trust_score if trust_score is not None else auth, 3),
            freshness=freshness_score(published_at, now),
            license=license_,
            metadata=metadata or {},
            provenance=provenance or {},
            security_status=report.status,
            security_flags=report.flags,
            pipeline_version=PIPELINE_VERSION,
        ).with_hash()

        # ── CHUNKING ─────────────────────────────────────────────────
        job = _move(job, IngestionState.CHUNKING)
        chunks = chunk_parsed(parsed, document_id)
        if not chunks:
            return _fail(job, "chunker produced no chunks", FailureCause.CHUNKING_FAILURE)

        # ── EMBEDDING (best-effort; lexical survives failures) ───────
        job = _move(job, IngestionState.EMBEDDING)
        embedded = 0
        if self._embedder is not None and self._vector is not None:
            for chunk in chunks:
                try:
                    emb = await self._embedder.embed(chunk.content[:2000])
                    embedding_id = await self._vector.add_knowledge_chunk(
                        chunk_id=chunk.chunk_id,
                        text=chunk.content,
                        embedding=emb,
                        metadata={
                            "source": uri or source_id,
                            "document_id": document_id,
                            "source_type": source_type.value,
                            "chunk_index": chunk.chunk_index,
                            "fabric": True,
                        },
                    )
                    chunks[chunks.index(chunk)] = chunk.model_copy(update={"embedding_id": embedding_id})
                    embedded += 1
                except Exception as exc:
                    _log.debug("fabric.chunk_embed_failed", event_type="knowledge", error=repr(exc))

        # ── INDEXING: SQL truth + lexical index ──────────────────────
        job = _move(job, IngestionState.INDEXING)
        await self._store.save_document(doc, chunks)
        for chunk in chunks:
            self._retriever.index_chunk(chunk, doc)

        _log.info(
            "fabric.ingested",
            event_type="knowledge",
            doc=document_id,
            source_type=source_type.value,
            chunks=len(chunks),
            embedded=embedded,
            security=report.status.value,
        )
        return job.model_copy(
            update={"state": IngestionState.READY, "document_id": document_id, "updated_ts": self._clock.now()}
        )

    async def ingest_file(self, path: Path, *, source_type: SourceType = SourceType.LOCAL_FILE) -> IngestionJob:
        """Local file entry point — text-readable files only (local-first, §22)."""
        try:
            content = await asyncio.to_thread(path.read_text, encoding="utf-8", errors="replace")
        except OSError as exc:
            now = self._clock.now()
            job = IngestionJob(
                job_id=f"job_{self._ids.execution_id()}",
                source=str(path),
                source_type=source_type,
                created_ts=now,
                updated_ts=now,
            )
            return _fail(job, f"unreadable: {exc!r}", FailureCause.PARSER_FAILURE)
        ext_mime = {
            ".md": "text/markdown",
            ".json": "application/json",
            ".csv": "text/csv",
            ".html": "text/html",
            ".yaml": "text/yaml",
            ".yml": "text/yaml",
        }
        return await self.ingest(
            source_id=str(path),
            source_type=source_type,
            content=content,
            title=path.name,
            uri=str(path),
            content_type=ext_mime.get(path.suffix.lower(), "text/plain"),
        )


def _move(job: IngestionJob, state: IngestionState) -> IngestionJob:
    return job.model_copy(update={"state": state})


def _fail(job: IngestionJob, error: str, cause: FailureCause) -> IngestionJob:
    _log.warning("fabric.ingest_failed", event_type="knowledge", error=error, cause=cause.value)
    return job.model_copy(update={"state": IngestionState.FAILED, "error": error, "failure_cause": cause})


def _canonical(uri: str) -> str:
    """Strip tracking params/fragments so same-content URIs dedupe."""
    if not uri:
        return ""
    try:
        p = urllib.parse.urlparse(uri)
        return urllib.parse.urlunparse((p.scheme, p.netloc.lower(), p.path, "", "", ""))
    except Exception:
        return uri
