"""CloudEmbedder — OpenAI-shaped /v1/embeddings parsing + fail-closed contract."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from atlas.memory.embedder import CloudEmbedder, EmbeddingError, FallbackEmbedder


def _resp(payload: dict, status: int = 200) -> MagicMock:
    r = MagicMock()
    r.status_code = status
    r.json.return_value = payload
    r.raise_for_status.return_value = None
    return r


async def test_parses_openai_shaped_response() -> None:
    emb = CloudEmbedder(base_url="https://api.jina.ai/v1", api_key="k", model="jina-embeddings-v3")
    payload = {"data": [{"embedding": [0.1, 0.2, 0.3]}]}
    with patch("httpx.AsyncClient.post", new=AsyncMock(return_value=_resp(payload))):
        vec = await emb.embed("hello")
    assert vec == [0.1, 0.2, 0.3]


async def test_sends_bearer_auth_and_model() -> None:
    emb = CloudEmbedder(base_url="https://api.jina.ai/v1", api_key="secret", model="jina-embeddings-v3")
    post = AsyncMock(return_value=_resp({"data": [{"embedding": [1.0, 2.0]}]}))
    with patch("httpx.AsyncClient.post", new=post):
        await emb.embed("hi")
    _, kwargs = post.call_args
    assert kwargs["headers"]["Authorization"] == "Bearer secret"
    assert kwargs["json"]["model"] == "jina-embeddings-v3"
    assert kwargs["json"]["input"] == "hi"


async def test_empty_data_raises() -> None:
    emb = CloudEmbedder(base_url="https://x/v1", api_key="k", model="m")
    with patch("httpx.AsyncClient.post", new=AsyncMock(return_value=_resp({"data": []}))):
        with pytest.raises(EmbeddingError):
            await emb.embed("hello")


async def test_zero_vector_raises() -> None:
    emb = CloudEmbedder(base_url="https://x/v1", api_key="k", model="m")
    payload = {"data": [{"embedding": [0.0] * 16}]}
    with patch("httpx.AsyncClient.post", new=AsyncMock(return_value=_resp(payload))):
        with pytest.raises(EmbeddingError):
            await emb.embed("hello")


async def test_http_error_becomes_embedding_error() -> None:
    emb = CloudEmbedder(base_url="https://x/v1", api_key="k", model="m")
    with patch("httpx.AsyncClient.post", new=AsyncMock(side_effect=Exception("boom"))):
        with pytest.raises(EmbeddingError):
            await emb.embed("hello")


async def test_missing_key_raises_without_network() -> None:
    emb = CloudEmbedder(base_url="https://x/v1", api_key="", model="m")
    with pytest.raises(EmbeddingError):
        await emb.embed("hello")


async def test_slots_into_fallback_embedder() -> None:
    good = CloudEmbedder(base_url="https://x/v1", api_key="k", model="m")
    payload = {"data": [{"embedding": [0.5, 0.6]}]}
    with patch("httpx.AsyncClient.post", new=AsyncMock(return_value=_resp(payload))):
        fb = FallbackEmbedder([good])
        vec = await fb.embed("hello")
    assert vec == [0.5, 0.6]
