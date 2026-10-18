"""Local BGE-M3 embeddings, optional cloud adapter, and embedding worker.

The composition root defaults to ``OllamaEmbedder``. Explicit cloud embedding
uses an OpenAI-shaped POST /embeddings client. Every live embedder declares its
vector-space identity, so changing backend/model opens separate collections
without treating equal dimensions as compatible embeddings.

Async embedding worker:
- Non-blocking: embeddings happen in background
- Queue-based: handles bursts gracefully
- Batching: processes multiple items efficiently
- Persistent: stores embeddings in vector DB
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Protocol
from urllib.parse import urlsplit, urlunsplit

import httpx

from atlas.infra.config import Settings
from atlas.infra.logging import get_logger

if TYPE_CHECKING:
    from atlas.infra.db import Database
    from atlas.memory.vectorstore import VectorStore

_log = get_logger("atlas.memory.embedder")


class Embedder(Protocol):
    async def embed(self, text: str) -> list[float]: ...
    async def close(self) -> None: ...


class EmbeddingError(Exception):
    """Raised when embedding generation fails. Never silently return zero vectors."""

    pass


def _embedding_identity(provider: str, endpoint: str, model: str) -> str:
    """Name a vector space without incorporating keys or URL credentials."""
    url = urlsplit(endpoint)
    host = url.hostname or ""
    if ":" in host:
        host = f"[{host}]"
    if url.port:
        host = f"{host}:{url.port}"
    canonical = urlunsplit((url.scheme.lower(), host, url.path.rstrip("/"), "", ""))
    return f"{provider}|{canonical}|{model}"


def embedding_space_of(embedder: Embedder) -> str:
    """Bootstrap must never attach an unidentified embedder to legacy vectors."""
    identity = getattr(embedder, "embedding_space", None)
    if not isinstance(identity, str) or not identity:
        raise EmbeddingError("embedder must declare its embedding_space before persistent vector storage is wired")
    return identity


class OllamaEmbedder:
    is_local = True

    def __init__(self, settings: Settings, timeout_s: float = 180.0) -> None:
        self._host = settings.ollama_host.rstrip("/")
        self._model = settings.embed_model
        model = self._model if ":" in self._model else f"{self._model}:latest"
        self.embedding_space = _embedding_identity("ollama", self._host, model)
        self.model_id = self.embedding_space
        # WHY explicit Timeout: bge-m3 needs ~50s to load on first request.
        # httpx.AsyncClient(timeout=N) sets ALL timeout phases to N seconds.
        # Using Timeout object makes it explicit and easier to tune per phase.
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(connect=10.0, read=timeout_s, write=10.0, pool=10.0),
            trust_env=False,
        )

    async def embed(self, text: str) -> list[float]:
        try:
            resp = await self._client.post(f"{self._host}/api/embed", json={"model": self._model, "input": text})
            resp.raise_for_status()
            data = resp.json()
            vec = data.get("embeddings", [[]])[0] if "embeddings" in data else data.get("embedding", [])
            if not vec or all(x == 0.0 for x in vec[:10]):
                raise EmbeddingError(f"Ollama returned empty/zero embedding for model {self._model}")
            return [float(x) for x in vec]
        except EmbeddingError:
            raise
        except Exception as exc:
            raise EmbeddingError(
                f"Ollama embedding failed (host={self._host}, model={self._model}): {type(exc).__name__}({exc})"
            ) from exc

    async def close(self) -> None:
        await self._client.aclose()


class CloudEmbedder:
    """Embeddings via a cloud embed API with an OpenAI-shaped endpoint.

    Explicit OpenRouter use can select ``qwen/qwen3-embedding-0.6b`` (POST
    {base_url}/embeddings, Authorization: Bearer <key>) using the chat key.
    Base URL and model are configurable. Equal dimensions never make its
    vectors interchangeable with BGE-M3: each model gets separate collections.

    Contract mirrors ``OllamaEmbedder``: never returns a silent zero vector —
    any empty/all-zero response or transport failure raises ``EmbeddingError``.
    """

    is_local = False

    def __init__(self, base_url: str, api_key: str, model: str, timeout_s: float = 90.0) -> None:
        self._base = base_url.rstrip("/")
        self._api_key = api_key
        self._model = model
        self.embedding_space = _embedding_identity("openai", self._base, model)
        self.model_id = self.embedding_space
        # First cloud call can be slow (cold model); split phases explicitly.
        self._client = httpx.AsyncClient(timeout=httpx.Timeout(connect=10.0, read=timeout_s, write=10.0, pool=10.0))

    async def embed(self, text: str) -> list[float]:
        if not self._api_key:
            raise EmbeddingError(f"CloudEmbedder has no API key (provider base={self._base}, model={self._model})")
        try:
            resp = await self._client.post(
                f"{self._base}/embeddings",
                headers={"Authorization": f"Bearer {self._api_key}"},
                json={"model": self._model, "input": text},
            )
            resp.raise_for_status()
            data = resp.json()
            items = data.get("data", [])
            vec = items[0].get("embedding", []) if items else []
            if not vec or all(x == 0.0 for x in vec[:10]):
                raise EmbeddingError(f"Cloud embed API returned empty/zero embedding for model {self._model}")
            return [float(x) for x in vec]
        except EmbeddingError:
            raise
        except Exception as exc:
            raise EmbeddingError(
                f"Cloud embedding failed (base={self._base}, model={self._model}): {type(exc).__name__}({exc})"
            ) from exc

    async def close(self) -> None:
        await self._client.aclose()


class FallbackEmbedder:
    """Tries multiple embedders in order. Never returns silent zero vectors.

    Usage: FallbackEmbedder([OllamaEmbedder(...), CloudEmbedder(...)])
    """

    def __init__(self, embedders: list[Embedder]) -> None:
        self._embedders = embedders

    async def embed(self, text: str) -> list[float]:
        last_error: Exception | None = None
        for embedder in self._embedders:
            try:
                return await embedder.embed(text)
            except EmbeddingError as exc:
                last_error = exc
                _log.warning(
                    "embedding.fallback",
                    event_type="memory",
                    embedder=type(embedder).__name__,
                    error=str(exc),
                )
        raise EmbeddingError(f"All embedders failed. Last error: {last_error}")

    async def close(self) -> None:
        for embedder in self._embedders:
            if hasattr(embedder, "close"):
                await embedder.close()


class EmbeddingWorker:
    """Async worker that processes embeddings in background without blocking writes."""

    def __init__(
        self,
        embedder: Embedder,
        vector_store: VectorStore,
        db: Database,
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
        """Stop the worker gracefully."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
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

                except TimeoutError:
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
                    "UPDATE episodes SET embedding_id = ? WHERE id = ?", (embedding_id, episode_id)
                )
                await self._db.conn.commit()

                _log.debug(
                    "embedding_worker.processed",
                    event_type="memory",
                    episode_id=episode_id,
                    embedding_id=embedding_id,
                )

            except Exception as exc:
                err_str = str(exc)
                if "404" in err_str:
                    _log.warning(
                        "embedding_worker.embed_unavailable",
                        event_type="memory",
                        episode_id=episode_id,
                        error="Provider returned 404. Embeddings skipped.",
                    )
                else:
                    _log.error(
                        "embedding_worker.embed_error",
                        event_type="memory",
                        episode_id=episode_id,
                        error=err_str,
                    )
