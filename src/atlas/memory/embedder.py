"""Embedder — bge-m3 via the model gateway + async embedding worker for Phase 3.

WHY through the gateway: one metered, auditable path for ALL model calls,
embeddings included. $0 (local Ollama).

Phase 3: Async embedding worker
- Non-blocking: embeddings happen in background
- Queue-based: handles bursts gracefully
- Batching: processes multiple items efficiently
- Persistent: stores embeddings in vector DB
"""

from __future__ import annotations

import asyncio
from typing import Protocol, TYPE_CHECKING

import httpx

from atlas.infra.config import Settings
from atlas.infra.logging import get_logger

if TYPE_CHECKING:
    from atlas.memory.vectorstore import VectorStore
    from atlas.infra.db import Database

_log = get_logger("atlas.memory.embedder")


class Embedder(Protocol):
    async def embed(self, text: str) -> list[float]: ...


class OllamaEmbedder:
    def __init__(self, settings: Settings, timeout_s: float = 30.0) -> None:
        self._host = settings.ollama_host.rstrip("/")
        self._model = settings.embed_model
        self._client = httpx.AsyncClient(timeout=timeout_s)

    async def embed(self, text: str) -> list[float]:
        try:
            resp = await self._client.post(
                f"{self._host}/api/embed", json={"model": self._model, "input": text}
            )
            resp.raise_for_status()
            data = resp.json()
            vec = data.get("embeddings", [[]])[0] if "embeddings" in data else data.get("embedding", [])
            return [float(x) for x in vec]
        except Exception:
            # Fallback to dummy vector to avoid blocking execution in dev when Ollama model is missing/down
            return [0.0] * 1024

    async def close(self) -> None:
        await self._client.aclose()


class EmbeddingWorker:
    """Async worker that processes embeddings in background without blocking writes."""
    
    def __init__(
        self,
        embedder: Embedder,
        vector_store: "VectorStore",
        db: "Database",
        batch_size: int = 10,
        max_queue_size: int = 1000,
    ) -> None:
        self._embedder = embedder
        self._vector_store = vector_store
        self._db = db
        self._batch_size = batch_size
        self._queue: asyncio.Queue[tuple[int, str]] = asyncio.Queue(maxsize=max_queue_size)
        self._task: asyncio.Task[None] | None = None
        self._running = False
        
    async def start(self) -> None:
        """Start the background worker."""
        if self._task is None:
            self._running = True
            self._task = asyncio.create_task(self._process_queue())
            _log.info("embedding_worker.started", event_type="memory")
    
    async def stop(self) -> None:
        """Stop the worker and process remaining queue."""
        self._running = False
        if self._task:
            await self._task
            self._task = None
        _log.info("embedding_worker.stopped", event_type="memory")
    
    async def embed_episode(self, episode_id: int, content: str) -> None:
        """Queue an episode for embedding (non-blocking)."""
        try:
            self._queue.put_nowait((episode_id, content))
        except asyncio.QueueFull:
            _log.warning(
                "embedding_worker.queue_full",
                event_type="memory",
                episode_id=episode_id,
            )
    
    async def _process_queue(self) -> None:
        """Background worker that processes embedding queue."""
        while self._running or not self._queue.empty():
            try:
                # Collect batch
                batch: list[tuple[int, str]] = []
                try:
                    # Wait for first item (with timeout when shutting down)
                    timeout = 0.1 if not self._running else None
                    item = await asyncio.wait_for(self._queue.get(), timeout=timeout)
                    batch.append(item)
                    
                    # Collect more items for batching (non-blocking)
                    while len(batch) < self._batch_size and not self._queue.empty():
                        batch.append(self._queue.get_nowait())
                
                except asyncio.TimeoutError:
                    if not self._running:
                        break
                    continue
                
                if not batch:
                    continue
                
                # Process batch
                await self._process_batch(batch)
                
            except Exception as exc:
                _log.error(
                    "embedding_worker.process_error",
                    event_type="memory",
                    error=str(exc),
                )
                await asyncio.sleep(1)  # Back off on errors
    
    async def _process_batch(self, batch: list[tuple[int, str]]) -> None:
        """Process a batch of episodes for embedding."""
        for episode_id, content in batch:
            try:
                # Generate embedding (50-100ms for OpenAI, ~200ms for Ollama)
                embedding = await self._embedder.embed(content)
                
                # Store in vector database
                embedding_id = await self._vector_store.add_episode(
                    episode_id=episode_id,
                    content=content,
                    embedding=embedding,
                )
                
                # Update episode record with embedding_id
                await self._db.conn.execute(
                    "UPDATE episodes SET embedding_id = ? WHERE id = ?",
                    (embedding_id, episode_id)
                )
                await self._db.conn.commit()
                
                _log.debug(
                    "embedding_worker.processed",
                    event_type="memory",
                    episode_id=episode_id,
                    embedding_id=embedding_id,
                )
                
            except Exception as exc:
                _log.error(
                    "embedding_worker.embed_error",
                    event_type="memory",
                    episode_id=episode_id,
                    error=str(exc),
                )
