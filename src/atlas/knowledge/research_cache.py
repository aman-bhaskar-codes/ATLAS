"""Research cache — local-first knowledge storage and retrieval.

WHY a research cache: agents frequently search for the same concepts, tools,
and APIs. Without caching, every search hits the web (or fails in offline mode).
The research cache stores search results, web scrapes, and tool documentation
locally in the vector store so subsequent queries hit local memory first.

ZERO-COST-FIRST: This is the foundation for fully offline research capability.
When ATLAS is in local_free mode, the research cache is the ONLY knowledge
source — there is no web search fallback.
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field
from typing import Any, Protocol

from atlas.infra.logging import get_logger
from atlas.memory.vectorstore import VectorHit

_log = get_logger("atlas.knowledge.cache")


class Embedder(Protocol):
    async def embed(self, text: str) -> list[float]: ...


class VectorStore(Protocol):
    async def add_knowledge_chunk(
        self, chunk_id: str, text: str, embedding: list[float], metadata: dict[str, Any]
    ) -> str: ...
    async def search_knowledge(self, query_embedding: list[float], k: int) -> list[VectorHit]: ...
    async def delete_knowledge_chunk(self, embedding_id: str) -> None: ...


@dataclass
class CacheEntry:
    """A cached research result."""

    query: str
    content: str
    source: str  # "web", "tool_doc", "user_doc", "rag"
    timestamp: float = field(default_factory=time.time)
    ttl_hours: float = 168.0  # 7 days default
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def chunk_id(self) -> str:
        return hashlib.sha256(f"{self.source}:{self.query}".encode()).hexdigest()[:16]

    @property
    def expired(self) -> bool:
        return (time.time() - self.timestamp) > self.ttl_hours * 3600


class ResearchCache:
    """Local-first research result cache backed by vector store.

    Usage:
        # Store a research result
        await cache.store("how to parse JSON in Python", content, source="web")

        # Retrieve cached results for a similar query
        hits = await cache.search("python JSON parsing", k=3)
    """

    def __init__(self, vector_store: VectorStore, embedder: Embedder) -> None:
        self._store = vector_store
        self._embedder = embedder
        self._stats = {"hits": 0, "misses": 0, "stores": 0}

    async def store(
        self,
        query: str,
        content: str,
        source: str = "web",
        ttl_hours: float = 168.0,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        """Store a research result in the cache."""
        entry = CacheEntry(
            query=query,
            content=content,
            source=source,
            ttl_hours=ttl_hours,
            metadata=metadata or {},
        )

        try:
            embedding = await self._embedder.embed(content[:2000])  # embed first 2K chars
            chunk_meta = {
                "source": source,
                "query": query[:200],
                "timestamp": entry.timestamp,
                "ttl_hours": ttl_hours,
                **entry.metadata,
            }
            embedding_id = await self._store.add_knowledge_chunk(
                chunk_id=entry.chunk_id,
                text=content,
                embedding=embedding,
                metadata=chunk_meta,
            )
            self._stats["stores"] += 1
            _log.debug(
                "research_cache.stored",
                event_type="knowledge",
                chunk_id=entry.chunk_id,
                source=source,
                content_len=len(content),
            )
            return embedding_id
        except Exception as exc:
            _log.warning("research_cache.store_failed", event_type="knowledge", error=str(exc))
            return ""

    async def search(
        self,
        query: str,
        k: int = 5,
        min_score: float = 0.5,
    ) -> list[VectorHit]:
        """Search the cache for relevant research results."""
        try:
            embedding = await self._embedder.embed(query)
            hits = await self._store.search_knowledge(embedding, k=k)
            # Filter by score threshold
            relevant = [h for h in hits if h.score >= min_score]
            if relevant:
                self._stats["hits"] += 1
            else:
                self._stats["misses"] += 1
            return relevant
        except Exception as exc:
            _log.warning("research_cache.search_failed", event_type="knowledge", error=str(exc))
            self._stats["misses"] += 1
            return []

    @property
    def stats(self) -> dict[str, int]:
        return dict(self._stats)
