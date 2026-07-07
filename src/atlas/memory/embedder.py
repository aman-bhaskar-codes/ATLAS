"""Embedder — bge-m3 via the model gateway. WHY through the gateway: one metered,
auditable path for ALL model calls, embeddings included. $0 (local Ollama)."""

from __future__ import annotations

from typing import Protocol

import httpx

from atlas.infra.config import Settings


class Embedder(Protocol):
    async def embed(self, text: str) -> list[float]: ...


class OllamaEmbedder:
    def __init__(self, settings: Settings, timeout_s: float = 30.0) -> None:
        self._host = settings.ollama_host.rstrip("/")
        self._model = settings.embed_model
        self._client = httpx.AsyncClient(timeout=timeout_s)

    async def embed(self, text: str) -> list[float]:
        resp = await self._client.post(
            f"{self._host}/api/embed", json={"model": self._model, "input": text}
        )
        resp.raise_for_status()
        data = resp.json()
        vec = data.get("embeddings", [[]])[0] if "embeddings" in data else data.get("embedding", [])
        return [float(x) for x in vec]

    async def close(self) -> None:
        await self._client.aclose()
