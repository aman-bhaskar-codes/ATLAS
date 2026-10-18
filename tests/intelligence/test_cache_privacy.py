"""Semantic cache policy and context isolation, using the real local store."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from types import SimpleNamespace

import pytest

from atlas.infra.db import Database
from atlas.infra.types import NetworkPolicy, PrivacyClass
from atlas.intelligence.cache import SemanticCache
from atlas.intelligence.contracts import Constraints, InferenceRequest, InferenceResponse, Message


class Embedder:
    def __init__(self) -> None:
        self.texts: list[str] = []

    async def embed(self, text: str) -> list[float]:
        self.texts.append(text)
        return [1.0]


class Vectors:
    def __init__(self) -> None:
        self.refs: list[str] = []

    async def query(self, embedding: list[float], k: int) -> list[SimpleNamespace]:
        return [SimpleNamespace(ref=ref, score=1.0) for ref in self.refs[:k]]

    async def upsert(self, ref: str, text: str, embedding: list[float]) -> None:
        self.refs.append(ref)


@pytest.fixture
async def database(tmp_path: Path) -> AsyncIterator[Database]:
    db = Database(tmp_path / "cache-policy.db")
    await db.start()
    yield db
    await db.stop()


def request(policy: Constraints | None = None, system: str = "You are concise.") -> InferenceRequest:
    return InferenceRequest(
        correlation_id="cache-policy",
        constraints=policy or Constraints(),
        messages=[Message(role="system", content=system), Message(role="user", content="question")],
    )


@pytest.mark.parametrize("privacy", [PrivacyClass.PRIVATE, PrivacyClass.SENSITIVE, PrivacyClass.SECRET])
async def test_private_requests_bypass_cloud_embedder_and_store(database: Database, privacy: PrivacyClass) -> None:
    embedder = Embedder()
    cache = SemanticCache(database, Vectors(), embedder)
    req = request(Constraints(privacy_class=privacy))
    await cache.put(req, InferenceResponse(text="private answer", model_id="local", provider="ollama"))
    assert await cache.get(req) is None
    assert embedder.texts == []
    row = await (await database.conn.execute("SELECT count(*) AS count FROM semantic_cache")).fetchone()
    assert row["count"] == 0


@pytest.mark.parametrize("network", [NetworkPolicy.OFFLINE, NetworkPolicy.LOCAL_ONLY])
async def test_local_network_policy_does_not_call_cloud_cache_embedder(
    database: Database, network: NetworkPolicy
) -> None:
    embedder = Embedder()
    cache = SemanticCache(database, Vectors(), embedder)
    req = request(Constraints(network_policy=network))
    assert await cache.get(req) is None
    await cache.put(req, InferenceResponse(text="answer", model_id="local", provider="ollama"))
    assert embedder.texts == []


async def test_semantic_hit_cannot_cross_policy_boundaries(database: Database) -> None:
    vectors = Vectors()
    cache = SemanticCache(database, vectors, Embedder())
    await cache.put(request(), InferenceResponse(text="cloud answer", model_id="cloud", provider="cloud"))
    different_policy = request(Constraints(max_latency_ms=50))
    assert await cache.get(different_policy) is None


async def test_semantic_hit_cannot_cross_system_instruction_boundaries(database: Database) -> None:
    cache = SemanticCache(database, Vectors(), Embedder())
    await cache.put(
        request(system="Return English."), InferenceResponse(text="hello", model_id="cloud", provider="cloud")
    )
    assert await cache.get(request(system="Return French.")) is None


async def test_output_parameters_do_not_reuse_a_prompt_only_entry(database: Database) -> None:
    cache = SemanticCache(database, Vectors(), Embedder())
    await cache.put(request(), InferenceResponse(text="long answer", model_id="cloud", provider="cloud"))
    req = request().model_copy(update={"max_tokens": 1})
    assert await cache.get(req) is None


@pytest.mark.parametrize("cost_policy", ["zero_cost", "free_only"])
async def test_free_cost_policy_does_not_pay_for_cache_embeddings(database: Database, cost_policy: str) -> None:
    embedder = Embedder()
    cache = SemanticCache(database, Vectors(), embedder)
    req = request(Constraints(cost_policy=cost_policy))
    assert await cache.get(req) is None
    await cache.put(req, InferenceResponse(text="answer", model_id="free", provider="cloud"))
    assert embedder.texts == []


async def test_local_private_cache_keeps_exact_hits_without_cloud_egress(database: Database) -> None:
    embedder = Embedder()
    cache = SemanticCache(database, Vectors(), embedder, embedding_is_local=True)
    req = request(
        Constraints(privacy_class=PrivacyClass.SECRET, network_policy=NetworkPolicy.LOCAL_ONLY, cost_policy="zero_cost")
    )
    await cache.put(req, InferenceResponse(text="local answer", model_id="local", provider="ollama"))
    count = len(embedder.texts)
    hit = await cache.get(req)
    assert hit is not None and hit.text == "local answer"
    assert len(embedder.texts) == count


async def test_semantic_match_with_same_policy_and_context_remains_available(database: Database) -> None:
    cache = SemanticCache(database, Vectors(), Embedder())
    await cache.put(request(), InferenceResponse(text="similar answer", model_id="cloud", provider="cloud"))
    paraphrase = request().model_copy(
        update={
            "messages": [
                Message(role="system", content="You are concise."),
                Message(role="user", content="paraphrased question"),
            ]
        }
    )
    hit = await cache.get(paraphrase)
    assert hit is not None and hit.text == "similar answer"
