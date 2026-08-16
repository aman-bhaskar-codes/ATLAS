"""Knowledge Store — Real-time document indexing for RAG.

Phase 3: Streaming document ingestion pipeline
- Async document processing with live progress updates
- Chunking strategies for different file types
- Instant embedding and indexing (searchable in seconds)
- Metadata extraction and enrichment
- Citation tracking for transparency

WHY separate from semantic memory: External documents are different from
agent-generated facts. They need: chunk management, source attribution,
update/delete operations, and full-text search alongside vector search.
"""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from atlas.infra.clock import Clock
from atlas.infra.db import Database
from atlas.infra.ids import IdGenerator
from atlas.infra.logging import get_logger
from atlas.memory.embedder import Embedder
from atlas.memory.vectorstore import VectorStore

if TYPE_CHECKING:
    from atlas.infra.bus import MessageBus

_log = get_logger("atlas.memory.knowledge_store")


@dataclass
class DocumentChunk:
    """A chunk of a document with metadata."""

    chunk_id: str
    document_id: str
    content: str
    chunk_index: int
    total_chunks: int
    metadata: dict[str, Any]


@dataclass
class Document:
    """Document metadata."""

    id: str
    title: str
    source_path: str
    source_type: str  # pdf, markdown, txt, web
    chunk_count: int
    file_hash: str
    created_ts: datetime
    indexed: bool = False


class KnowledgeStore:
    """Real-time document indexing with streaming progress."""

    def __init__(
        self,
        db: Database,
        vector_store: VectorStore,
        embedder: Embedder,
        ids: IdGenerator,
        clock: Clock,
    ) -> None:
        self._db = db
        self._vector_store = vector_store
        self._embedder = embedder
        self._ids = ids
        self._clock = clock
        self._bus: MessageBus | None = None

        # Chunking parameters
        self._chunk_size = 512  # tokens
        self._chunk_overlap = 50  # tokens

    def set_bus(self, bus: MessageBus) -> None:
        """Connect to event bus for progress broadcasting."""
        self._bus = bus
        _log.info("knowledge_store.bus_connected", event_type="memory")

    async def ingest_document(
        self,
        file_path: Path,
        source_type: str,
        metadata: dict[str, Any] | None = None,
        progress_callback: Callable[[dict[str, Any]], Any] | None = None,
    ) -> str:
        """
        Ingest a document with streaming progress updates.

        Returns: document_id
        Target: 5-10 seconds for 100-page PDF
        """
        _log.info("knowledge_store.ingest_started", event_type="memory", path=str(file_path), type=source_type)

        # Step 1: Create document record
        doc_id = self._ids.execution_id()
        file_hash = await self._compute_hash(file_path)

        # Check if already indexed
        existing = await self._get_document_by_hash(file_hash)
        if existing:
            _log.info(
                "knowledge_store.duplicate_detected", event_type="memory", doc_id=existing.id, path=str(file_path)
            )
            return existing.id

        # Step 2: Parse and chunk document (streaming)
        chunks: list[DocumentChunk] = []
        async for chunk in self._parse_document_streaming(file_path, doc_id, source_type, metadata or {}):
            chunks.append(chunk)

            # Progress update
            if progress_callback:
                await progress_callback({"status": "chunking", "chunks_processed": len(chunks), "document_id": doc_id})

        # Step 3: Create document record
        doc = Document(
            id=doc_id,
            title=metadata.get("title", file_path.name) if metadata else file_path.name,
            source_path=str(file_path),
            source_type=source_type,
            chunk_count=len(chunks),
            file_hash=file_hash,
            created_ts=self._clock.now(),
            indexed=False,
        )

        await self._save_document(doc)

        # Step 4: Embed and index chunks in parallel
        embedding_tasks = []
        for chunk in chunks:
            task = asyncio.create_task(self._embed_and_index_chunk(chunk, progress_callback))
            embedding_tasks.append(task)

        # Wait for all embeddings
        await asyncio.gather(*embedding_tasks)

        # Step 5: Mark as indexed
        await self._mark_indexed(doc_id)

        _log.info("knowledge_store.ingest_complete", event_type="memory", doc_id=doc_id, chunks=len(chunks))

        if progress_callback:
            await progress_callback({"status": "complete", "document_id": doc_id, "chunks": len(chunks)})

        return doc_id

    async def _compute_hash(self, file_path: Path) -> str:
        """Compute file hash for deduplication."""
        hasher = hashlib.sha256()
        with open(file_path, "rb") as f:
            while chunk := f.read(8192):
                hasher.update(chunk)
        return hasher.hexdigest()

    async def _get_document_by_hash(self, file_hash: str) -> Document | None:
        """Check if document already exists."""
        cur = await self._db.conn.execute(
            """
            SELECT id, title, source_path, source_type, chunk_count, file_hash,
                   indexed, created_ts, updated_ts
            FROM knowledge_documents WHERE file_hash = ?
            """,
            (file_hash,),
        )
        row = await cur.fetchone()
        if not row:
            return None

        return Document(
            id=row["id"],
            title=row["title"],
            source_path=row["source_path"],
            source_type=row["source_type"],
            chunk_count=row["chunk_count"],
            file_hash=row["file_hash"],
            created_ts=datetime.fromisoformat(row["created_ts"]),
            indexed=bool(row["indexed"]),
        )

    async def _parse_document_streaming(
        self, file_path: Path, doc_id: str, source_type: str, metadata: dict[str, Any]
    ) -> Any:  # AsyncGenerator[DocumentChunk, None]
        """Parse document into chunks (streaming)."""

        if source_type == "markdown" or source_type == "txt":
            # Simple text chunking
            async for chunk in self._chunk_text_file(file_path, doc_id, metadata):
                yield chunk

        elif source_type == "pdf":
            # PDF parsing (would use pypdf or similar)
            async for chunk in self._chunk_pdf_file(file_path, doc_id, metadata):
                yield chunk

        else:
            _log.warning("knowledge_store.unsupported_type", event_type="memory", type=source_type)
            return

    async def _chunk_text_file(
        self, file_path: Path, doc_id: str, metadata: dict[str, Any]
    ) -> Any:  # AsyncGenerator[DocumentChunk, None]
        """Chunk a text file."""
        content = file_path.read_text(encoding="utf-8")

        # Simple chunking by character count
        # TODO: Use smarter semantic chunking
        chunk_char_size = self._chunk_size * 4  # ~4 chars per token
        overlap_chars = self._chunk_overlap * 4

        chunks: list[DocumentChunk] = []
        start = 0
        while start < len(content):
            end = start + chunk_char_size
            chunk_text = content[start:end]

            chunk = DocumentChunk(
                chunk_id=f"{doc_id}_chunk_{len(chunks)}",
                document_id=doc_id,
                content=chunk_text,
                chunk_index=len(chunks),
                total_chunks=0,  # Will update later
                metadata={**metadata, "start_char": start, "end_char": end},
            )
            chunks.append(chunk)
            yield chunk

            start = end - overlap_chars

        # Update total_chunks for all chunks
        for chunk in chunks:
            chunk.total_chunks = len(chunks)

    async def _chunk_pdf_file(
        self, file_path: Path, doc_id: str, metadata: dict[str, Any]
    ) -> Any:  # AsyncGenerator[DocumentChunk, None]
        """Chunk a PDF file."""
        # TODO: Implement PDF parsing with pypdf
        # For now, treat as text
        _log.info("knowledge_store.pdf_fallback", event_type="memory", path=str(file_path))
        async for chunk in self._chunk_text_file(file_path, doc_id, metadata):
            yield chunk

    async def _embed_and_index_chunk(
        self, chunk: DocumentChunk, progress_callback: Callable[[dict[str, Any]], Any] | None = None
    ) -> None:
        """Embed and index a single chunk (async, non-blocking)."""
        try:
            # Generate embedding
            embedding = await self._embedder.embed(chunk.content)

            # Store in vector database (get embedding_id)
            embedding_id = await self._vector_store.add_knowledge_chunk(
                chunk_id=chunk.chunk_id, text=chunk.content, embedding=embedding, metadata=chunk.metadata
            )

            # Store chunk metadata in SQL database
            import json

            now_iso = self._clock.now().isoformat()
            await self._db.conn.execute(
                """
                INSERT INTO knowledge_chunks
                (chunk_id, document_id, content, chunk_index, total_chunks, 
                 embedding_id, metadata_json, created_ts)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    chunk.chunk_id,
                    chunk.document_id,
                    chunk.content,
                    chunk.chunk_index,
                    chunk.total_chunks,
                    embedding_id,
                    json.dumps(chunk.metadata),
                    now_iso,
                ),
            )
            await self._db.conn.commit()

            _log.debug(
                "knowledge_store.chunk_indexed", event_type="memory", chunk_id=chunk.chunk_id, doc_id=chunk.document_id
            )

            if progress_callback:
                await progress_callback(
                    {"status": "indexing", "chunk_indexed": chunk.chunk_index + 1, "total_chunks": chunk.total_chunks}
                )

        except Exception as exc:
            _log.error("knowledge_store.chunk_error", event_type="memory", chunk_id=chunk.chunk_id, error=str(exc))

    async def _save_document(self, doc: Document) -> None:
        """Save document metadata to database."""
        now_iso = self._clock.now().isoformat()
        await self._db.conn.execute(
            """
            INSERT INTO knowledge_documents 
            (id, title, source_path, source_type, chunk_count, file_hash, indexed, created_ts, updated_ts)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                doc.id,
                doc.title,
                doc.source_path,
                doc.source_type,
                doc.chunk_count,
                doc.file_hash,
                0,  # Not indexed yet
                now_iso,
                now_iso,
            ),
        )
        await self._db.conn.commit()

        _log.info("knowledge_store.document_saved", event_type="memory", doc_id=doc.id, title=doc.title)

    async def _mark_indexed(self, doc_id: str) -> None:
        """Mark document as fully indexed."""
        await self._db.conn.execute(
            """
            UPDATE knowledge_documents 
            SET indexed = 1, updated_ts = ?
            WHERE id = ?
            """,
            (self._clock.now().isoformat(), doc_id),
        )
        await self._db.conn.commit()

        _log.info("knowledge_store.marked_indexed", event_type="memory", doc_id=doc_id)

    async def search(self, query: str, limit: int = 5, filters: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        """
        Search knowledge base (< 100ms).

        Returns: List of chunks with metadata
        """
        # Generate query embedding
        query_embedding = await self._embedder.embed(query)

        # Search vector store
        hits = await self._vector_store.search_knowledge(query_embedding, k=limit * 2)

        # Enrich with database metadata
        results = []
        for hit in hits[:limit]:
            # Extract chunk_id from embedding_id (format: kc_{chunk_id})
            chunk_id = hit.ref.replace("kc_", "")

            # Get chunk metadata from database
            cur = await self._db.conn.execute(
                """
                SELECT kc.chunk_id, kc.document_id, kc.content, kc.chunk_index,
                       kc.total_chunks, kc.metadata_json,
                       kd.title, kd.source_path, kd.source_type
                FROM knowledge_chunks kc
                JOIN knowledge_documents kd ON kc.document_id = kd.id
                WHERE kc.chunk_id = ?
                """,
                (chunk_id,),
            )
            row = await cur.fetchone()

            if row:
                import json

                results.append(
                    {
                        "chunk_id": row["chunk_id"],
                        "document_id": row["document_id"],
                        "content": row["content"],
                        "chunk_index": row["chunk_index"],
                        "total_chunks": row["total_chunks"],
                        "score": hit.score,
                        "document_title": row["title"],
                        "source_path": row["source_path"],
                        "source_type": row["source_type"],
                        "metadata": json.loads(row["metadata_json"]) if row["metadata_json"] else {},
                    }
                )

        return results

    async def delete_document(self, doc_id: str) -> None:
        """Delete a document and all its chunks."""
        # Get all chunks for this document
        cur = await self._db.conn.execute(
            "SELECT chunk_id, embedding_id FROM knowledge_chunks WHERE document_id = ?", (doc_id,)
        )
        rows = list(await cur.fetchall())

        # Delete from vector store
        for row in rows:
            if row["embedding_id"]:
                await self._vector_store.delete_knowledge_chunk(row["embedding_id"])

        # Delete from SQL database (CASCADE will delete chunks)
        await self._db.conn.execute("DELETE FROM knowledge_documents WHERE id = ?", (doc_id,))
        await self._db.conn.commit()

        _log.info("knowledge_store.document_deleted", event_type="memory", doc_id=doc_id, chunks_deleted=len(rows))

    async def list_documents(self, limit: int = 50, offset: int = 0) -> list[Document]:
        """List all indexed documents."""
        cur = await self._db.conn.execute(
            """
            SELECT id, title, source_path, source_type, chunk_count, file_hash,
                   indexed, created_ts, updated_ts
            FROM knowledge_documents
            ORDER BY created_ts DESC
            LIMIT ? OFFSET ?
            """,
            (limit, offset),
        )
        rows = await cur.fetchall()

        return [
            Document(
                id=row["id"],
                title=row["title"],
                source_path=row["source_path"],
                source_type=row["source_type"],
                chunk_count=row["chunk_count"],
                file_hash=row["file_hash"],
                created_ts=datetime.fromisoformat(row["created_ts"]),
                indexed=bool(row["indexed"]),
            )
            for row in rows
        ]
