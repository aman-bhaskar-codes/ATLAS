"""The gateway applies profile policy before either cache or model egress."""

from __future__ import annotations

import base64
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from atlas.infra.db import Database
from atlas.infra.types import (
    CostClass,
    CostPolicy,
    ImageInput,
    ModelCapability,
    ModelRequest,
    ModelTarget,
    NetworkPolicy,
    PrivacyClass,
    ToolCallSpec,
)
from atlas.intelligence.cache import SemanticCache
from atlas.intelligence.contracts import Constraints, InferenceRequest, InferenceResponse, Message, ModelSpec
from atlas.intelligence.errors import RoutingError
from atlas.intelligence.gateway import ModelGateway
from atlas.intelligence.health.health_monitor import HealthMonitor
from atlas.intelligence.registry.capability_index import CapabilityIndex
from atlas.intelligence.registry.model_registry import ModelRegistry
from atlas.intelligence.runtime.fallback import FallbackEngine
from atlas.intelligence.selection.router import CapabilityRouter
from atlas.intelligence.selection.selector import ModelSelector


class RecordingRuntime:
    def __init__(self) -> None:
        self.requests: list[InferenceRequest] = []

    async def attempt(self, req: InferenceRequest, spec: ModelSpec) -> InferenceResponse:
        self.requests.append(req)
        return InferenceResponse(text="fresh", model_id=spec.id, provider=spec.provider)


class RecordingEmbedder:
    def __init__(self) -> None:
        self.texts: list[str] = []

    async def embed(self, text: str) -> list[float]:
        self.texts.append(text)
        return [0.5]


class EmptyVectors:
    async def query(self, embedding: list[float], k: int) -> list[object]:
        return []

    async def upsert(self, ref: str, text: str, embedding: list[float]) -> None:
        pass


@pytest.fixture
async def database(tmp_path: Path) -> AsyncIterator[Database]:
    db = Database(tmp_path / "gateway.db")
    await db.start()
    yield db
    await db.stop()


def model(name: str, cost: CostClass, caps: frozenset[ModelCapability] | None = None) -> ModelSpec:
    return ModelSpec(
        id=name,
        provider=name,
        provider_model=name,
        cost_class=cost,
        context_length=8192,
        usd_per_1m_input=0,
        usd_per_1m_output=0,
        quality_score=0.1 if cost == CostClass.LOCAL else 0.99,
        capabilities=caps if caps is not None else frozenset({ModelCapability.REASONING}),
    )


def gateway(
    specs: list[ModelSpec],
    runtime: RecordingRuntime,
    defaults: Constraints | None = None,
    cache: SemanticCache | None = None,
) -> ModelGateway:
    registry = ModelRegistry({spec.id: spec for spec in specs})
    return ModelGateway(
        router=CapabilityRouter(),
        selector=ModelSelector(CapabilityIndex(registry), HealthMonitor()),
        fallback=FallbackEngine(),
        runtime=runtime,  # type: ignore[arg-type]
        default_constraints=defaults,
        cache=cache,
    )


def request(constraints: Constraints | None = None, **kwargs: object) -> InferenceRequest:
    return InferenceRequest(
        correlation_id="privacy-check",
        messages=[Message(role="user", content="same prompt")],
        constraints=constraints or Constraints(),
        **kwargs,  # type: ignore[arg-type]
    )


@pytest.mark.parametrize("legacy", [False, True])
async def test_caller_cannot_loosen_profile_policy(legacy: bool) -> None:
    runtime = RecordingRuntime()
    subject = gateway(
        [model("local", CostClass.LOCAL), model("paid", CostClass.PAID)],
        runtime,
        Constraints(
            cost_policy=CostPolicy.ZERO_COST,
            network_policy=NetworkPolicy.LOCAL_ONLY,
            privacy_class=PrivacyClass.SECRET,
        ),
    )
    if legacy:
        response = await subject.complete(
            ModelRequest(
                correlation_id="legacy",
                prompt="same prompt",
                required_capabilities=frozenset({ModelCapability.REASONING}),
            )
        )
        assert response.model == "local"
    else:
        response = await subject.infer(request())
        assert response.model_id == "local"
    effective = runtime.requests[0].constraints
    assert effective.cost_policy == CostPolicy.ZERO_COST
    assert effective.network_policy == NetworkPolicy.LOCAL_ONLY
    assert effective.privacy_class == PrivacyClass.SECRET


@pytest.mark.parametrize("legacy", [False, True])
async def test_caller_can_tighten_each_policy(legacy: bool) -> None:
    runtime = RecordingRuntime()
    subject = gateway([model("local", CostClass.LOCAL), model("cloud", CostClass.FREE_QUOTA)], runtime)
    policies = dict(
        cost_policy=CostPolicy.ZERO_COST, network_policy=NetworkPolicy.OFFLINE, privacy_class=PrivacyClass.SECRET
    )
    if legacy:
        await subject.complete(
            ModelRequest(
                correlation_id="legacy",
                prompt="same prompt",
                required_capabilities=frozenset({ModelCapability.REASONING}),
                **policies,
            )
        )
    else:
        await subject.infer(request(Constraints(**policies)))
    effective = runtime.requests[0].constraints
    assert effective.cost_policy == CostPolicy.ZERO_COST
    assert effective.network_policy == NetworkPolicy.OFFLINE
    assert effective.privacy_class == PrivacyClass.SECRET


async def test_cache_cannot_short_circuit_current_model_eligibility(database: Database) -> None:
    embedder = RecordingEmbedder()
    cache = SemanticCache(database, EmptyVectors(), embedder)
    await cache.put(request(), InferenceResponse(text="old cloud", model_id="cloud", provider="cloud"))
    subject = gateway([model("local", CostClass.LOCAL)], RecordingRuntime(), cache=cache)
    response = await subject.infer(request())
    assert response.model_id == "local"
    assert response.text == "fresh"


async def test_routing_failure_precedes_cache_or_embedding(database: Database) -> None:
    embedder = RecordingEmbedder()
    cache = SemanticCache(database, EmptyVectors(), embedder)
    await cache.put(request(), InferenceResponse(text="old", model_id="local", provider="local"))
    embedder.texts.clear()
    subject = gateway([model("local", CostClass.LOCAL)], RecordingRuntime(), cache=cache)
    with pytest.raises(RoutingError):
        await subject.infer(request(required_capabilities=frozenset({ModelCapability.VISION})))
    assert embedder.texts == []


async def test_cached_source_provider_must_match_current_model(database: Database) -> None:
    cache = SemanticCache(database, EmptyVectors(), RecordingEmbedder())
    await cache.put(request(), InferenceResponse(text="wrong source", model_id="local", provider="cloud"))
    subject = gateway([model("local", CostClass.LOCAL)], RecordingRuntime(), cache=cache)
    response = await subject.infer(request())
    assert response.provider == "local"
    assert response.text == "fresh"


async def test_secret_request_never_reaches_cloud_cache_embedding(database: Database) -> None:
    embedder = RecordingEmbedder()
    cache = SemanticCache(database, EmptyVectors(), embedder)
    subject = gateway([model("local", CostClass.LOCAL)], RecordingRuntime(), cache=cache)
    await subject.infer(request(Constraints(privacy_class=PrivacyClass.SECRET)))
    assert embedder.texts == []


@pytest.mark.parametrize("kind", ["tools", "images"])
async def test_tools_and_images_do_not_reuse_prompt_only_cache(database: Database, kind: str) -> None:
    runtime = RecordingRuntime()
    embedder = RecordingEmbedder()
    cache = SemanticCache(database, EmptyVectors(), embedder)
    caps = frozenset({ModelCapability.REASONING, ModelCapability.TOOL_CALLING, ModelCapability.VISION})
    subject = gateway([model("local", CostClass.LOCAL, caps)], runtime, cache=cache)
    if kind == "tools":
        req = request(tools=[ToolCallSpec(name="filesystem")])
    else:
        image = ImageInput(mime_type="image/png", data=base64.b64encode(b"\x89PNG\r\n\x1a\nimage").decode())
        req = request().model_copy(update={"messages": [Message(role="user", content="same prompt", images=(image,))]})
    await subject.infer(req)
    await subject.infer(req)
    assert len(runtime.requests) == 2
    assert embedder.texts == []


async def test_legacy_images_reach_vision_model_and_keep_cloud_provenance() -> None:
    runtime = RecordingRuntime()
    image = ImageInput(mime_type="image/png", data=base64.b64encode(b"\x89PNG\r\n\x1a\nimage").decode())
    subject = gateway(
        [
            model("text", CostClass.LOCAL),
            model("vision", CostClass.FREE_QUOTA, frozenset({ModelCapability.REASONING, ModelCapability.VISION})),
        ],
        runtime,
    )
    response = await subject.complete(
        ModelRequest(
            correlation_id="image",
            prompt="read image",
            images=(image,),
            required_capabilities=frozenset({ModelCapability.REASONING}),
        )
    )
    assert response.model == "vision"
    assert runtime.requests[0].messages[-1].images == (image,)
    assert ModelCapability.VISION in runtime.requests[0].required_capabilities
    assert response.target == ModelTarget.CLOUD


async def test_free_cloud_response_is_not_reported_as_local() -> None:
    subject = gateway([model("cloud", CostClass.FREE_QUOTA)], RecordingRuntime())
    response = await subject.complete(
        ModelRequest(
            correlation_id="cloud", prompt="hello", required_capabilities=frozenset({ModelCapability.REASONING})
        )
    )
    assert response.cost.usd == 0
    assert response.target == ModelTarget.CLOUD
