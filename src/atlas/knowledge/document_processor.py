"""Document processor — local-first document ingestion for RAG.

WHY local processing: in zero-cost mode, ATLAS can't call cloud document
processing APIs. This module handles chunking and ingestion of local documents
(text, markdown, code, PDF text) into the vector store for semantic retrieval.

Chunking strategy: fixed-size overlapping windows with boundary-aware splitting.
No ML-based chunking (that would require a model call) — simple, reliable,
and fast enough for local use.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from atlas.infra.logging import get_logger
from atlas.knowledge.chunking import chunk_parsed
from atlas.knowledge.parsers import parse_text

_log = get_logger("atlas.knowledge.documents")


class Embedder(Protocol):
    async def embed(self, text: str) -> list[float]: ...


class VectorStore(Protocol):
    async def add_knowledge_chunk(
        self, chunk_id: str, text: str, embedding: list[float], metadata: dict[str, Any]
    ) -> str: ...
    async def search_knowledge(self, query_embedding: list[float], k: int) -> list[Any]: ...


# Supported file extensions → MIME types
_SUPPORTED = {
    ".txt": "text/plain",
    ".md": "text/markdown",
    ".py": "text/x-python",
    ".js": "text/javascript",
    ".ts": "text/typescript",
    ".json": "application/json",
    ".yaml": "text/yaml",
    ".yml": "text/yaml",
    ".toml": "text/toml",
    ".cfg": "text/plain",
    ".ini": "text/plain",
    ".sh": "text/x-shellscript",
    ".html": "text/html",
    ".css": "text/css",
    ".sql": "text/x-sql",
    ".rs": "text/x-rust",
    ".go": "text/x-go",
    ".java": "text/x-java",
    ".csv": "text/csv",
    ".log": "text/plain",
    ".env": "text/plain",
}


@dataclass(frozen=True)
class Chunk:
    """A text chunk ready for embedding."""

    chunk_id: str
    text: str
    source_path: str
    chunk_index: int
    total_chunks: int
    metadata: dict[str, Any]


class DocumentProcessor:
    """Process local documents into chunks for vector store ingestion.

    Usage:
        processor = DocumentProcessor(vector_store, embedder)
        stats = await processor.ingest_file(Path("docs/guide.md"))
        stats = await processor.ingest_directory(Path("docs/"))
    """

    def __init__(
        self,
        vector_store: VectorStore,
        embedder: Embedder,
        *,
        chunk_size: int = 1000,
        chunk_overlap: int = 200,
        max_file_size_mb: float = 10.0,
    ) -> None:
        if chunk_size <= 0 or not 0 <= chunk_overlap < chunk_size:
            raise ValueError("chunk size must be positive and 0 <= overlap < chunk size")
        self._store = vector_store
        self._embedder = embedder
        self._chunk_size = chunk_size
        self._overlap = chunk_overlap
        self._max_bytes = int(max_file_size_mb * 1024 * 1024)

    def _chunk_text(self, text: str, source: str) -> list[Chunk]:
        """Split text into overlapping chunks with boundary awareness."""
        if not text.strip():
            return []

        windows = chunk_parsed(parse_text(text), source, max_chars=self._chunk_size, overlap=self._overlap)
        return [
            Chunk(
                chunk_id=hashlib.sha256(f"{source}:{c.chunk_index}:{c.content}".encode()).hexdigest()[:16],
                text=c.content,
                source_path=source,
                chunk_index=c.chunk_index,
                total_chunks=c.total_chunks,
                metadata={"total_chunks": c.total_chunks, "char_start": c.char_start, "char_end": c.char_end},
            )
            for c in windows
        ]

    async def ingest_file(self, path: Path) -> dict[str, Any]:
        """Ingest a single file into the vector store.

        Returns stats: {chunks, embedded, errors, skipped}.
        """
        stats = {"chunks": 0, "embedded": 0, "errors": 0, "skipped": False}

        if not path.exists():
            _log.warning("doc.not_found", event_type="knowledge", path=str(path))
            stats["skipped"] = True
            return stats

        ext = path.suffix.lower()
        if ext not in _SUPPORTED:
            _log.debug("doc.unsupported", event_type="knowledge", path=str(path), ext=ext)
            stats["skipped"] = True
            return stats

        file_size = path.stat().st_size
        if file_size > self._max_bytes:
            _log.warning("doc.too_large", event_type="knowledge", path=str(path), bytes=file_size)
            stats["skipped"] = True
            return stats

        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except Exception as exc:
            _log.warning("doc.read_error", event_type="knowledge", path=str(path), error=str(exc))
            stats["errors"] += 1
            return stats

        chunks = self._chunk_text(text, str(path))
        stats["chunks"] = len(chunks)

        for chunk in chunks:
            try:
                embedding = await self._embedder.embed(chunk.text[:2000])
                await self._store.add_knowledge_chunk(
                    chunk_id=chunk.chunk_id,
                    text=chunk.text,
                    embedding=embedding,
                    metadata={
                        "source": str(path),
                        "chunk_index": chunk.chunk_index,
                        "total_chunks": chunk.total_chunks,
                        "file_type": _SUPPORTED.get(ext, "text/plain"),
                    },
                )
                stats["embedded"] += 1
            except Exception as exc:
                _log.warning(
                    "doc.embed_error",
                    event_type="knowledge",
                    chunk_id=chunk.chunk_id,
                    error=str(exc),
                )
                stats["errors"] += 1

        _log.info(
            "doc.ingested",
            event_type="knowledge",
            path=str(path),
            chunks=stats["chunks"],
            embedded=stats["embedded"],
        )
        return stats

    async def ingest_directory(
        self,
        directory: Path,
        *,
        recursive: bool = True,
        extensions: set[str] | None = None,
    ) -> dict[str, Any]:
        """Ingest all supported files in a directory.

        Returns aggregate stats.
        """
        total = {"files": 0, "chunks": 0, "embedded": 0, "errors": 0, "skipped": 0}
        allowed = extensions or set(_SUPPORTED.keys())

        pattern = "**/*" if recursive else "*"
        for path in sorted(directory.glob(pattern)):
            if not path.is_file():
                continue
            if path.suffix.lower() not in allowed:
                continue
            # Skip hidden files and common build artifacts
            if any(part.startswith(".") for part in path.parts):
                continue
            if any(part in {"node_modules", "__pycache__", ".git", "dist", "build"} for part in path.parts):
                continue

            total["files"] += 1
            stats = await self.ingest_file(path)
            total["chunks"] += stats["chunks"]
            total["embedded"] += stats["embedded"]
            total["errors"] += stats["errors"]
            if stats.get("skipped"):
                total["skipped"] += 1

        _log.info(
            "doc.directory_ingested",
            event_type="knowledge",
            directory=str(directory),
            **total,
        )
        return total

    @staticmethod
    def supported_extensions() -> list[str]:
        """List of file extensions this processor can handle."""
        return sorted(_SUPPORTED.keys())
