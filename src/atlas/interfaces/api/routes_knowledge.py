"""Knowledge Store REST API routes.

Phase 3: Document ingestion, search, and management endpoints
"""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from pydantic import BaseModel, Field

from atlas.app import Atlas
from atlas.infra.types import ToolRequest
from atlas.interfaces.api.auth import require_admin
from atlas.interfaces.api.dependencies import get_atlas
from atlas.interfaces.notify import ConfirmationRequiredError
from atlas.knowledge.deletion import DeletionScope
from atlas.knowledge.domain import IngestionState, KnowledgeDocument, SourceType
from atlas.knowledge.ocr import MAX_DOCUMENT_BYTES, page_provenance
from atlas.knowledge.reranking import FeatureReranker
from atlas.safety.engine import DeniedError, HaltedError
from atlas.tools.paths import PathError, resolve_in_allowlist

router = APIRouter(prefix="/api/v1/knowledge", tags=["knowledge"])


def _get_atlas(request: Request) -> Atlas:
    """Convenience wrapper for get_atlas dependency."""
    return get_atlas(request)


class IngestRequest(BaseModel):
    """Request to ingest a document from a path."""

    source_path: str = Field(min_length=1, max_length=4096)
    source_type: str  # markdown, pdf, image, txt, web
    title: str | None = None


class IngestResponse(BaseModel):
    """Response from document ingestion."""

    document_id: str
    title: str
    chunks: int
    indexed: bool


class SearchRequest(BaseModel):
    """Request to search knowledge base."""

    query: str = Field(min_length=1, max_length=4000)
    limit: int = Field(default=5, ge=1, le=50)


class ReindexRequest(BaseModel):
    limit_documents: int = Field(default=10, ge=1, le=50)


@router.post("/reindex", dependencies=[Depends(require_admin)])
async def reindex_corpus(request: ReindexRequest, atlas: Atlas = Depends(_get_atlas)) -> dict[str, int]:
    """Refresh one bounded embedding batch while retaining source documents."""
    fabric = _fabric(atlas)
    if fabric is None:
        raise HTTPException(503, "The research corpus pipeline is unavailable.")
    if not fabric.pipeline.embedding_reindex_available:
        raise HTTPException(
            503, "Configure a compatible embedding model and vector store before refreshing embeddings."
        )
    result: dict[str, int] = await fabric.pipeline.reindex_embeddings(
        limit_documents=request.limit_documents, timeout_s=30.0
    )
    return result


class SearchResult(BaseModel):
    """A single search result."""

    chunk_id: str
    document_id: str
    document_title: str
    content: str
    score: float
    chunk_index: int
    total_chunks: int
    source_path: str
    source_type: str
    page_start: int | None = None
    page_end: int | None = None
    extraction_method: str | None = None


class SearchResponse(BaseModel):
    """Response from knowledge search."""

    query: str
    results: list[SearchResult]
    total: int


class DocumentListItem(BaseModel):
    """Document list item."""

    id: str
    title: str
    source_path: str
    source_type: str
    chunk_count: int
    indexed: bool
    created_ts: str


class DocumentListResponse(BaseModel):
    """Response from listing documents."""

    documents: list[DocumentListItem]
    total: int


def _fabric(atlas: Atlas) -> Any:
    return getattr(atlas, "knowledge_fabric", None)


def _authorized_file(atlas: Atlas, raw: str) -> Path:
    manifest = getattr(atlas, "manifest", None)
    globs = manifest.allowed_paths.get("read", []) if manifest is not None else []
    try:
        path = resolve_in_allowlist(raw, globs).host
        for raw_dir in manifest.safety.get("credential_dirs", []) if manifest is not None else []:
            sensitive = Path(raw_dir).expanduser().resolve()
            if path == sensitive or sensitive in path.parents:
                raise PathError("credential directories cannot be ingested")
        return path
    except (PathError, OSError, RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=403, detail="source path is not permitted for knowledge ingestion") from exc


async def _ingest_file(
    atlas: Atlas, path: Path, source_type: str, *, title: str = "", source_uri: str = ""
) -> IngestResponse:
    fabric = _fabric(atlas)
    if fabric is not None:
        job = await fabric.pipeline.ingest_file(
            path,
            source_type=SourceType.USER_PROVIDED if source_uri else SourceType.LOCAL_FILE,
            title=title,
            source_uri=source_uri,
        )
        if job.state is not IngestionState.READY:
            status = 503 if "unavailable" in job.error.lower() else 422
            raise HTTPException(status_code=status, detail=job.error or "document ingestion failed")
        doc = await fabric.store.get_document(job.document_id)
        if doc is None:
            raise HTTPException(status_code=500, detail="indexed document not found")
        refs = await fabric.store.chunk_refs_for_document(doc.document_id)
        return IngestResponse(document_id=doc.document_id, title=doc.title, chunks=len(refs), indexed=bool(refs))
    store = atlas.knowledge_store
    doc_id = await store.ingest_document(path, source_type, metadata={"title": title or path.name})
    docs = await store.list_documents(limit=10000, offset=0)
    doc = next((d for d in docs if d.id == doc_id), None)
    if doc is None:
        raise HTTPException(status_code=500, detail="document created but not found")
    return IngestResponse(document_id=doc.id, title=doc.title, chunks=doc.chunk_count, indexed=doc.indexed)


@router.post("/ingest", response_model=IngestResponse)
async def ingest_document(request: IngestRequest, atlas: Atlas = Depends(_get_atlas)) -> IngestResponse:
    """Ingest an authorized local document into the shared research corpus."""
    path = _authorized_file(atlas, request.source_path)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="source file not found")
    return await _ingest_file(atlas, path, request.source_type, title=request.title or "")


@router.post("/ingest/upload", response_model=IngestResponse)
async def ingest_upload(
    file: UploadFile = File(...), atlas: Atlas = Depends(_get_atlas), source_type: str = "txt", title: str | None = None
) -> IngestResponse:
    """Bounded uploads retain the original source name in research citations."""
    name = Path(file.filename or "document.txt").name
    with tempfile.TemporaryDirectory(prefix="atlas-ingest-") as directory:
        path = Path(directory) / ("document" + Path(name).suffix.lower())
        total = 0
        with path.open("wb") as handle:
            while chunk := await file.read(64 * 1024):
                total += len(chunk)
                if total > MAX_DOCUMENT_BYTES:
                    raise HTTPException(status_code=413, detail="document exceeds upload size limit")
                await asyncio.to_thread(handle.write, chunk)
        return await _ingest_file(
            atlas, path, source_type, title=title or name, source_uri=f"upload://{quote(name, safe='')}"
        )


@router.post("/search", response_model=SearchResponse)
async def search_knowledge(request: SearchRequest, atlas: Atlas = Depends(_get_atlas)) -> SearchResponse:
    """
    Search knowledge base by semantic similarity.

    Returns top-K chunks ranked by vector similarity.
    Sub-100ms target for typical queries.
    """
    fabric = _fabric(atlas)
    if fabric is not None:
        result = await fabric.retriever.retrieve(request.query, k=max(20, request.limit * 2))
        ranked = FeatureReranker().rerank(request.query, result.candidates, k=request.limit)
        matches = [
            SearchResult(
                chunk_id=c.chunk.chunk_id,
                document_id=c.document.document_id,
                document_title=c.document.title,
                content=c.chunk.content,
                score=c.rrf_score,
                chunk_index=c.chunk.chunk_index,
                total_chunks=c.chunk.total_chunks,
                source_path=c.document.uri or c.document.source_id,
                source_type=c.document.source_type.value,
                **page_provenance(c.document.provenance, c.chunk.char_start, c.chunk.char_end),
            )
            for c in ranked
        ]
        return SearchResponse(query=request.query, results=matches, total=len(matches))
    store = atlas.knowledge_store
    results = await store.search(request.query, limit=request.limit)

    return SearchResponse(
        query=request.query,
        results=[
            SearchResult(
                chunk_id=r["chunk_id"],
                document_id=r["document_id"],
                document_title=r["document_title"],
                content=r["content"],
                score=r["score"],
                chunk_index=r["chunk_index"],
                total_chunks=r["total_chunks"],
                source_path=r["source_path"],
                source_type=r["source_type"],
            )
            for r in results
        ],
        total=len(results),
    )


@router.get("/documents", response_model=DocumentListResponse)
async def list_documents(atlas: Atlas = Depends(_get_atlas), limit: int = 50, offset: int = 0) -> DocumentListResponse:
    """
    List all indexed documents.

    Paginated response ordered by creation time (newest first).
    """
    fabric = _fabric(atlas)
    if fabric is not None:
        docs = await fabric.store.list_documents(limit=limit, offset=offset)
        items = []
        for document in docs:
            refs = await fabric.store.chunk_refs_for_document(document.document_id)
            items.append(_document_item(document, len(refs)))
        return DocumentListResponse(documents=items, total=len(items))
    store = atlas.knowledge_store
    docs = await store.list_documents(limit=limit, offset=offset)

    return DocumentListResponse(
        documents=[
            DocumentListItem(
                id=doc.id,
                title=doc.title,
                source_path=doc.source_path,
                source_type=doc.source_type,
                chunk_count=doc.chunk_count,
                indexed=doc.indexed,
                created_ts=doc.created_ts.isoformat(),
            )
            for doc in docs
        ],
        total=len(docs),
    )


@router.delete("/documents/{document_id}")
async def delete_document(document_id: str, atlas: Atlas = Depends(_get_atlas)) -> dict[str, str]:
    """
    Delete a document and all its chunks.

    Removes from both SQL database and vector store.
    """
    fabric = _fabric(atlas)
    if fabric is not None:
        await forget_research(ForgetRequest(scope="document", target=document_id), atlas)
        return {"status": "deleted", "document_id": document_id}
    store = atlas.knowledge_store
    await store.delete_document(document_id)

    return {"status": "deleted", "document_id": document_id}


@router.get("/documents/{document_id}")
async def get_document(document_id: str, atlas: Atlas = Depends(_get_atlas)) -> DocumentListItem:
    """Get a single document by ID."""
    fabric = _fabric(atlas)
    if fabric is not None:
        doc = await fabric.store.get_document(document_id)
        if doc is None:
            raise HTTPException(status_code=404, detail="document not found")
        refs = await fabric.store.chunk_refs_for_document(document_id)
        return _document_item(doc, len(refs))
    store = atlas.knowledge_store

    # Legacy compatibility path.
    docs = await store.list_documents(limit=10000, offset=0)
    doc = next((d for d in docs if d.id == document_id), None)

    if not doc:
        raise HTTPException(status_code=404, detail=f"Document not found: {document_id}")

    return DocumentListItem(
        id=doc.id,
        title=doc.title,
        source_path=doc.source_path,
        source_type=doc.source_type,
        chunk_count=doc.chunk_count,
        indexed=doc.indexed,
        created_ts=doc.created_ts.isoformat(),
    )


def _document_item(doc: KnowledgeDocument, chunks: int) -> DocumentListItem:
    return DocumentListItem(
        id=doc.document_id,
        title=doc.title,
        source_path=doc.uri or doc.source_id,
        source_type=doc.source_type.value,
        chunk_count=chunks,
        indexed=bool(chunks),
        created_ts=doc.retrieved_at.isoformat(),
    )


# --- Research-corpus forget (the one destructive knowledge operation) ------- #
#
# WHY two paths, and WHY the commit is not a bare store call:
#   * PREVIEW is read-only. `dry_run=True` computes per-store counts without
#     mutating anything (§22), so it needs no approval — previewing what a
#     forget would remove is part of the approval flow, not the deletion.
#   * COMMIT is funnel-governed: it dispatches through `safety.guard(...)` so
#     the policy engine, audit log, and kill switch all see it. A narrow forget
#     (evidence/chunk/document) is auto-approved; a corpus-wide forget
#     (source_type / uri / all) or a cascading-session forget is escalated to
#     DANGEROUS and requires a confirmation code — driven by the
#     `mass_research_deletion` matcher — not by anything in this file.
# Both paths touch ONLY the research corpus; personal trusted memory is never
# reached. (§11.)
_FORGET_SCOPES = ("evidence", "chunk", "document", "session", "source_type", "uri", "all")


class ForgetRequest(BaseModel):
    """Request to forget research memory at a given scope."""

    scope: str  # one of _FORGET_SCOPES
    target: str = ""
    cascade_documents: bool = False


class DeletionReportResponse(BaseModel):
    """Honest per-store outcome of a forget (§22) — never inflated coverage."""

    scope: str
    target: str
    dry_run: bool
    documents: int
    chunks: int
    evidence: int
    sessions: int
    vectors: int
    vectors_failed: int
    lexical: int
    notes: list[str]
    summary: str


def _research_memory(atlas: Atlas) -> object | None:
    """The ResearchMemory coordinator, or None when the fabric is unavailable."""
    fabric = getattr(atlas, "knowledge_fabric", None)
    return getattr(fabric, "research_memory", None) if fabric is not None else None


def _validate_scope(scope: str) -> DeletionScope:
    """Reject unknown scopes at the edge (400) before touching the funnel."""
    key = (scope or "").strip().lower()
    try:
        return DeletionScope(key)
    except ValueError as exc:
        raise HTTPException(
            status_code=400, detail=f"unknown forget scope: {scope!r}; expected one of {list(_FORGET_SCOPES)}"
        ) from exc


def _report_to_response(report: object) -> DeletionReportResponse:
    """Flatten a DeletionReport structurally (no knowledge-type import needed)."""
    scope = getattr(report, "scope", "")
    return DeletionReportResponse(
        scope=str(getattr(scope, "value", scope)),
        target=str(getattr(report, "target", "")),
        dry_run=bool(getattr(report, "dry_run", False)),
        documents=int(getattr(report, "documents", 0) or 0),
        chunks=int(getattr(report, "chunks", 0) or 0),
        evidence=int(getattr(report, "evidence", 0) or 0),
        sessions=int(getattr(report, "sessions", 0) or 0),
        vectors=int(getattr(report, "vectors", 0) or 0),
        vectors_failed=int(getattr(report, "vectors_failed", 0) or 0),
        lexical=int(getattr(report, "lexical", 0) or 0),
        notes=list(getattr(report, "notes", []) or []),
        summary=str(getattr(report, "summary", "")),
    )


@router.post("/research/forget/preview", response_model=DeletionReportResponse)
async def preview_forget(request: ForgetRequest, atlas: Atlas = Depends(_get_atlas)) -> DeletionReportResponse:
    """Preview a research forget WITHOUT deleting anything (read-only, §22).

    Returns the real per-store counts a commit would remove. Mutates nothing, so
    it is not approval-gated — this is the step you show a user before they issue
    the destructive DELETE below.
    """
    memory = _research_memory(atlas)
    if memory is None:
        raise HTTPException(status_code=503, detail="research memory unavailable")
    scope = _validate_scope(request.scope)
    report = await memory.forget(  # type: ignore[attr-defined]
        scope, request.target, cascade_documents=request.cascade_documents, dry_run=True
    )
    return _report_to_response(report)


@router.delete("/research", response_model=DeletionReportResponse)
async def forget_research(request: ForgetRequest, atlas: Atlas = Depends(_get_atlas)) -> DeletionReportResponse:
    """Permanently forget research memory at the given scope — funnel-governed.

    Irreversible. The request is classified, audited and confirmed by the
    SafetyEngine exactly like any tool dispatch; a corpus-wide (all/source_type/
    uri) or cascading-session forget is escalated to DANGEROUS and needs a
    confirmation code. Personal trusted memory is never touched (§11).
    """
    tool = atlas.tools.get("knowledge")
    if tool is None or _research_memory(atlas) is None:
        raise HTTPException(status_code=503, detail="research memory unavailable")
    _validate_scope(request.scope)  # 400 early; the funnel re-parses the string
    req = ToolRequest(
        correlation_id=atlas.ids.correlation_id(),
        tool="knowledge",
        operation="forget",
        args={
            "operation": "forget",
            "scope": request.scope,
            "target": request.target,
            "cascade_documents": request.cascade_documents,
        },
    )
    try:
        result = await atlas.safety.guard(req, tool)
    except ConfirmationRequiredError as exc:
        # The safety funnel reached a DANGEROUS tier and the CLI confirmer had
        # no TTY to prompt on (HTTP request). Surface this as a 4xx challenge
        # the caller can resolve out-of-band and retry, instead of a 500
        # EOFError from reading stdin.
        raise HTTPException(
            status_code=409,
            detail={
                "error": "confirmation_required",
                "confirmation_required": True,
                "approval_id": exc.approval_id,
                "tier": exc.tier.name,
                "matched_rule": exc.matched_rule,
                "reason": exc.reason,
                "prompt": exc.prompt,
                "tool": req.tool,
                "operation": req.operation,
                "args": req.args,
            },
        ) from exc
    except DeniedError as exc:
        raise HTTPException(
            status_code=403, detail=f"forget denied (tier {exc.decision.tier.name}): {exc.decision.reason}"
        ) from exc
    except HaltedError as exc:
        raise HTTPException(status_code=503, detail=f"forget halted: {exc}") from exc
    if not result.ok:
        raise HTTPException(status_code=500, detail=result.error or "forget failed")
    # execute() returns the already-flattened DeletionReport payload.
    return DeletionReportResponse(**result.output)
