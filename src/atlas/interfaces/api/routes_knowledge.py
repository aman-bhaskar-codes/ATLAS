"""Knowledge Store REST API routes.

Phase 3: Document ingestion, search, and management endpoints
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, UploadFile, File, Request, Depends
from pydantic import BaseModel

from atlas.app import Atlas
from atlas.interfaces.api.dependencies import get_atlas
from atlas.memory.knowledge_store import Document as KnowledgeDocument

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
async def ingest_document(
    request: IngestRequest,
    atlas: Atlas = Depends(_get_atlas)
) -> IngestResponse:
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
        file_path,
        request.source_type,
        metadata={"title": request.title or file_path.stem}
    )
    
    # Get document details
    docs = await store.list_documents(limit=1, offset=0)
    doc = next((d for d in docs if d.id == doc_id), None)
    
    if not doc:
        raise HTTPException(status_code=500, detail="Document created but not found")
    
    return IngestResponse(
        document_id=doc.id,
        title=doc.title,
        chunks=doc.chunk_count,
        indexed=doc.indexed
    )


@router.post("/ingest/upload", response_model=IngestResponse)
async def ingest_upload(
    file: UploadFile = File(...),
    atlas: Atlas = Depends(_get_atlas),
    source_type: str = "txt",
    title: str | None = None
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
            tmp_path,
            source_type,
            metadata={"title": title or file.filename or "Uploaded Document"}
        )
        
        # Get document details
        docs = await store.list_documents(limit=1, offset=0)
        doc = next((d for d in docs if d.id == doc_id), None)
        
        if not doc:
            raise HTTPException(status_code=500, detail="Document created but not found")
        
        return IngestResponse(
            document_id=doc.id,
            title=doc.title,
            chunks=doc.chunk_count,
            indexed=doc.indexed
        )
    finally:
        # Clean up temp file
        tmp_path.unlink(missing_ok=True)


@router.post("/search", response_model=SearchResponse)
async def search_knowledge(
    request: SearchRequest,
    atlas: Atlas = Depends(_get_atlas)
) -> SearchResponse:
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
                source_type=r["source_type"]
            )
            for r in results
        ],
        total=len(results)
    )


@router.get("/documents", response_model=DocumentListResponse)
async def list_documents(
    atlas: Atlas = Depends(_get_atlas),
    limit: int = 50,
    offset: int = 0
) -> DocumentListResponse:
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
                created_ts=doc.created_ts.isoformat()
            )
            for doc in docs
        ],
        total=len(docs)
    )


@router.delete("/documents/{document_id}")
async def delete_document(
    document_id: str,
    atlas: Atlas = Depends(_get_atlas)
) -> dict[str, str]:
    """
    Delete a document and all its chunks.
    
    Removes from both SQL database and vector store.
    """
    store = atlas.knowledge_store
    
    await store.delete_document(document_id)
    
    return {"status": "deleted", "document_id": document_id}


@router.get("/documents/{document_id}")
async def get_document(
    document_id: str,
    atlas: Atlas = Depends(_get_atlas)
) -> DocumentListItem:
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
        created_ts=doc.created_ts.isoformat()
    )
