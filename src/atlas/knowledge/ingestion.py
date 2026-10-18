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
from dataclasses import asdict
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
from atlas.knowledge.ocr import DocumentExtractionError, LocalDocumentExtractor, page_provenance
from atlas.knowledge.parsers import Parsed, parse_content
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
        extractor: LocalDocumentExtractor | None = None,
    ) -> None:
        self._store = store
        self._retriever = retriever
        self._ids = ids
        self._clock = clock
        self._embedder = embedder
        self._vector = vector
        self._extractor = extractor or LocalDocumentExtractor()
        self._embedding_space = str(getattr(embedder, "embedding_space", ""))

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
        parsed_document: Parsed | None = None,
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
        existing = await self._store.find_document_by_hash(h, source_type=source_type)
        compatible = not self._embedding_space or (
            existing is not None and existing.metadata.get("embedding_space") == self._embedding_space
        )
        if existing is not None and existing.pipeline_version == PIPELINE_VERSION:
            aliases = list(existing.provenance.get("source_aliases", [existing.canonical_uri or existing.source_id]))
            alias = _canonical(uri or source_id)
            if alias and alias not in aliases and len(aliases) < 20:
                aliases.append(alias)
                await self._store.update_document_provenance(
                    existing.document_id, {**existing.provenance, "source_aliases": aliases}
                )
            if not compatible and self._embedder is not None and self._vector is not None:
                try:
                    async with asyncio.timeout(60.0):
                        _, _, complete = await self._embed_document(existing)
                    if complete:
                        await self._retriever.rebuild()
                except TimeoutError:
                    _log.warning("fabric.namespace_refresh_timed_out", event_type="knowledge")
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
            parsed = parsed_document or parse_content(content, content_type=content_type, uri=uri, title=title)
        except Exception as exc:
            return _fail(job, f"parser error: {exc!r}", FailureCause.PARSER_FAILURE)
        if not parsed.text.strip():
            return _fail(job, "parser produced no text", FailureCause.PARSER_FAILURE)

        # ── NORMALIZING: canonical document + security verdict ───────
        job = _move(job, IngestionState.NORMALIZING)
        document_id = (
            existing.document_id if existing is not None else make_document_id(source_type, uri or source_id, now)
        )
        safe_metadata = {key: value for key, value in (metadata or {}).items() if key != "embedding_space"}
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
            metadata=safe_metadata,
            provenance={**(provenance or {}), "source_aliases": [_canonical(uri or source_id)]},
            security_status=report.status,
            security_flags=report.flags,
            pipeline_version=PIPELINE_VERSION,
        ).with_hash()

        # ── CHUNKING ─────────────────────────────────────────────────
        job = _move(job, IngestionState.CHUNKING)
        try:
            chunks = chunk_parsed(parsed, document_id)
        except ValueError as exc:
            return _fail(job, str(exc), FailureCause.CHUNKING_FAILURE)
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
                            "embedding_space": self._embedding_space,
                            **page_provenance(doc.provenance, chunk.char_start, chunk.char_end),
                        },
                    )
                    chunks[chunks.index(chunk)] = chunk.model_copy(update={"embedding_id": embedding_id})
                    embedded += 1
                except Exception as exc:
                    _log.debug("fabric.chunk_embed_failed", event_type="knowledge", error=repr(exc))

        if self._embedding_space and embedded == len(chunks):
            doc = doc.model_copy(update={"metadata": {**doc.metadata, "embedding_space": self._embedding_space}})

        # ── INDEXING: SQL truth + lexical index ──────────────────────
        job = _move(job, IngestionState.INDEXING)
        self._retriever.drop_document(document_id)
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

    @property
    def embedding_reindex_available(self) -> bool:
        return bool(self._embedding_space and self._embedder is not None and self._vector is not None)

    async def reindex_embeddings(self, *, limit_documents: int = 10, timeout_s: float = 60.0) -> dict[str, int]:
        """Rebuild a bounded batch in the current model namespace from SQL truth.

        Source text, IDs, page provenance, evidence, sessions, and old model
        collections remain intact. Only fully embedded documents get stamped.
        Failure leaves lexical retrieval available and the document retryable.
        """
        if not 1 <= limit_documents <= 50 or timeout_s <= 0:
            raise ValueError("reindex requires 1-50 documents and a positive timeout")
        stats = {"documents": 0, "embedded": 0, "errors": 0, "timed_out": 0}
        if not self._embedding_space or self._embedder is None or self._vector is None:
            return stats
        docs = await self._store.documents_needing_embedding_space(self._embedding_space, limit=limit_documents)
        try:
            async with asyncio.timeout(timeout_s):
                for doc in docs:
                    embedded, errors, complete = await self._embed_document(doc)
                    stats["embedded"] += embedded
                    stats["errors"] += errors
                    stats["documents"] += int(complete)
        except TimeoutError:
            stats["timed_out"] = 1
        if stats["documents"]:
            await self._retriever.rebuild()
        return stats

    async def _embed_document(self, doc: KnowledgeDocument) -> tuple[int, int, bool]:
        assert self._embedder is not None and self._vector is not None
        chunks = await self._store.chunks_for_document(doc.document_id)
        indexed: list[tuple[str, str]] = []
        errors = 0
        for chunk in chunks:
            try:
                embedding = await self._embedder.embed(chunk.content[:2000])
                ref = await self._vector.add_knowledge_chunk(
                    chunk.chunk_id,
                    chunk.content,
                    embedding,
                    {
                        "source": doc.uri or doc.source_id,
                        "document_id": doc.document_id,
                        "source_type": doc.source_type.value,
                        "chunk_index": chunk.chunk_index,
                        "fabric": True,
                        "embedding_space": self._embedding_space,
                        **page_provenance(doc.provenance, chunk.char_start, chunk.char_end),
                    },
                )
                indexed.append((chunk.chunk_id, ref))
            except Exception:
                errors += 1
        complete = bool(indexed) and len(indexed) == len(chunks)
        if complete:
            await self._store.update_document_embeddings(doc, indexed, self._embedding_space)
        return len(indexed), errors, complete

    async def ingest_file(
        self,
        path: Path,
        *,
        source_type: SourceType = SourceType.LOCAL_FILE,
        title: str = "",
        source_uri: str = "",
    ) -> IngestionJob:
        """Bounded local extraction, including text-first PDF and scanned images."""
        uri = source_uri or str(path)
        try:
            parsed = await self._extractor.extract(path)
        except (DocumentExtractionError, OSError) as exc:
            now = self._clock.now()
            job = IngestionJob(
                job_id=f"job_{self._ids.execution_id()}",
                source=uri,
                source_type=source_type,
                created_ts=now,
                updated_ts=now,
            )
            return _fail(job, str(exc), FailureCause.PARSER_FAILURE)
        ext_mime = {
            ".pdf": "application/pdf",
            ".png": "image/png",
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".webp": "image/webp",
            ".gif": "image/gif",
            ".tif": "image/tiff",
            ".tiff": "image/tiff",
            ".md": "text/markdown",
            ".json": "application/json",
            ".csv": "text/csv",
            ".html": "text/html",
            ".yaml": "text/yaml",
            ".yml": "text/yaml",
        }
        return await self.ingest(
            source_id=uri,
            source_type=source_type,
            content=parsed.text,
            title=title or parsed.title or path.name,
            uri=uri,
            content_type=str(parsed.metadata.get("content_type") or ext_mime.get(path.suffix.lower(), "text/plain")),
            parsed_document=parsed,
            provenance={"pages": [asdict(page) for page in parsed.pages]},
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
        host = p.hostname or ""
        netloc = f"[{host.lower()}]" if ":" in host else host.lower()
        if p.port is not None:
            netloc += f":{p.port}"
        return urllib.parse.urlunparse((p.scheme, netloc, p.path, "", "", ""))
    except ValueError:
        return ""
