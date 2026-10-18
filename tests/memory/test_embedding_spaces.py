"""Embedding migrations isolate vector spaces while preserving legacy data."""

from __future__ import annotations

from pathlib import Path

from atlas.infra.config import Settings
from atlas.memory.embedder import CloudEmbedder, OllamaEmbedder
from atlas.memory.vectorstore import ChromaVectorStore


async def test_model_namespaces_isolate_all_collections_without_deleting_legacy(tmp_path: Path) -> None:
    path = str(tmp_path / "chroma")
    legacy = ChromaVectorStore(path)
    await legacy.upsert("legacy", "legacy fact", [1.0, 0.0])
    await legacy.add_episode(1, "legacy episode", [1.0, 0.0])
    await legacy.add_knowledge_chunk("legacy", "legacy document", [1.0, 0.0], {"source": "legacy"})
    old = ChromaVectorStore(path, embedding_space="openai|https://openrouter.ai/api/v1|qwen/qwen3-embedding-0.6b")
    await old.upsert("cloud", "cloud fact", [1.0, 0.0])
    await old.add_episode(2, "cloud episode", [1.0, 0.0])
    await old.add_knowledge_chunk("cloud", "cloud document", [1.0, 0.0], {"source": "cloud"})
    local = ChromaVectorStore(path, embedding_space="ollama|http://localhost:11434|bge-m3:latest")

    assert await local.query([1.0, 0.0], 1) == []
    assert await local.search_episodes([1.0, 0.0], 1) == []
    assert await local.search_knowledge([1.0, 0.0], 1) == []
    await local.upsert("local", "local fact", [1.0, 0.0])
    await local.add_episode(3, "local episode", [1.0, 0.0])
    await local.add_knowledge_chunk("local", "local document", [1.0, 0.0], {"source": "local"})

    assert [hit.ref for hit in await old.query([1.0, 0.0], 1)] == ["cloud"]
    assert [hit.ref for hit in await old.search_episodes([1.0, 0.0], 1)] == ["ep_2"]
    assert [hit.ref for hit in await old.search_knowledge([1.0, 0.0], 1)] == ["kc_cloud"]
    assert [hit.ref for hit in await legacy.query([1.0, 0.0], 1)] == ["legacy"]
    assert [hit.ref for hit in await legacy.search_episodes([1.0, 0.0], 1)] == ["ep_1"]
    assert [hit.ref for hit in await legacy.search_knowledge([1.0, 0.0], 1)] == ["kc_legacy"]
    reopened = ChromaVectorStore(path, embedding_space=local.embedding_space)
    assert [hit.ref for hit in await reopened.query([1.0, 0.0], 1)] == ["local"]


async def test_embedding_identity_is_stable_and_contains_no_endpoint_secrets() -> None:
    cloud = CloudEmbedder(
        base_url="https://user:password@example.com/v1?api_key=secret#fragment",
        api_key="secret-key",
        model="embed-v1",
    )
    clean_cloud = CloudEmbedder(base_url="https://example.com/v1", api_key="other-key", model="embed-v1")
    settings = Settings(_env_file=None, embed_model="bge-m3", OPENROUTER_API_KEY="")  # type: ignore[call-arg]
    local = OllamaEmbedder(settings)
    try:
        assert cloud.embedding_space == "openai|https://example.com/v1|embed-v1"
        assert cloud.embedding_space == clean_cloud.embedding_space
        assert local.embedding_space == "ollama|http://localhost:11434|bge-m3:latest"
        assert local.model_id == local.embedding_space
    finally:
        await cloud.close()
        await clean_cloud.close()
        await local.close()
