"""Knowledge Store REST API routes.

Phase 3: Document ingestion, search, and management endpoints
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from pydantic import BaseModel

from atlas.app import Atlas
from atlas.infra.types import ToolRequest
from atlas.interfaces.api.dependencies import get_atlas
from atlas.interfaces.notify import ConfirmationRequiredError
from atlas.knowledge.deletion import DeletionScope
from atlas.safety.engine import DeniedError, HaltedError

router = APIRouter(prefix="/api/v1/knowledge", tags=["knowledge"])


def _get_atlas(request: Request) -> Atlas:
    """Convenience wrapper for get_atlas dependency."""
    return get_atlas(request)


class IngestRequest(BaseModel):
    """Request to ingest a document from a path."""

    source_path: str
    source_type: str  # markdown, pdf, txt, web
    title: str | None = None


class IngestResponse(BaseModel):
    """Response from document ingestion."""

    document_id: str
    title: str
    chunks: int
    indexed: bool


class SearchRequest(BaseModel):
    """Request to search knowledge base."""

    query: str
    limit: int = 5


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


@router.post("/ingest", response_model=IngestResponse)
async def ingest_document(request: IngestRequest, atlas: Atlas = Depends(_get_atlas)) -> IngestResponse:
    """
    Ingest a document from filesystem path.

    Streams the document, chunks it, embeds chunks, and indexes for search.
    Returns immediately with document_id; indexing continues in background.
    """
    store = atlas.knowledge_store

    file_path = Path(request.source_path)
    if not file_path.exists():
        raise HTTPException(status_code=404, detail=f"File not found: {request.source_path}")

    # Ingest with progress updates (could stream via WebSocket)
    doc_id = await store.ingest_document(
        file_path, request.source_type, metadata={"title": request.title or file_path.stem}
    )

    # Get document details
    docs = await store.list_documents(limit=1, offset=0)
    doc = next((d for d in docs if d.id == doc_id), None)

    if not doc:
        raise HTTPException(status_code=500, detail="Document created but not found")

    return IngestResponse(document_id=doc.id, title=doc.title, chunks=doc.chunk_count, indexed=doc.indexed)


@router.post("/ingest/upload", response_model=IngestResponse)
async def ingest_upload(
    file: UploadFile = File(...), atlas: Atlas = Depends(_get_atlas), source_type: str = "txt", title: str | None = None
) -> IngestResponse:
    """
    Upload and ingest a document.

    Accepts file upload, saves temporarily, then ingests.
    """
    store = atlas.knowledge_store

    # Save uploaded file temporarily
    import tempfile

    with tempfile.NamedTemporaryFile(delete=False, suffix=Path(file.filename or "doc").suffix) as tmp:
        content = await file.read()
        tmp.write(content)
        tmp_path = Path(tmp.name)

    try:
        # Ingest
        doc_id = await store.ingest_document(
            tmp_path, source_type, metadata={"title": title or file.filename or "Uploaded Document"}
        )

        # Get document details
        docs = await store.list_documents(limit=1, offset=0)
        doc = next((d for d in docs if d.id == doc_id), None)

        if not doc:
            raise HTTPException(status_code=500, detail="Document created but not found")

        return IngestResponse(document_id=doc.id, title=doc.title, chunks=doc.chunk_count, indexed=doc.indexed)
    finally:
        # Clean up temp file
        tmp_path.unlink(missing_ok=True)


@router.post("/search", response_model=SearchResponse)
async def search_knowledge(request: SearchRequest, atlas: Atlas = Depends(_get_atlas)) -> SearchResponse:
    """
    Search knowledge base by semantic similarity.

    Returns top-K chunks ranked by vector similarity.
    Sub-100ms target for typical queries.
    """
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
    store = atlas.knowledge_store

    await store.delete_document(document_id)

    return {"status": "deleted", "document_id": document_id}


@router.get("/documents/{document_id}")
async def get_document(document_id: str, atlas: Atlas = Depends(_get_atlas)) -> DocumentListItem:
    """Get a single document by ID."""
    store = atlas.knowledge_store

    # Query for this specific document
    docs = await store.list_documents(limit=1, offset=0)
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
