"""Provider readiness uses real adapters with a controlled HTTP transport."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from atlas.infra.config import Settings
from atlas.infra.types import CostClass, CostPolicy, NetworkPolicy, PrivacyClass, ProviderToolCall
from atlas.intelligence.capabilities import Capability
from atlas.intelligence.contracts import Constraints, Message, Role
from atlas.intelligence.errors import ProviderError
from atlas.intelligence.health.health_monitor import HealthMonitor
from atlas.intelligence.providers.gemini import GeminiProvider
from atlas.intelligence.providers.ollama import OllamaProvider
from atlas.intelligence.registry.capability_index import CapabilityIndex
from atlas.intelligence.registry.model_registry import ModelRegistry
from atlas.intelligence.registry.provider_registry import ProviderRegistry
from atlas.intelligence.selection.selector import ModelSelector

CONFIG = Path(__file__).resolve().parents[2] / "config"


async def test_gemini_auth_and_tool_metadata_survive_two_http_turns() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "candidates": [
                    {
                        "content": {
                            "parts": [
                                {"text": "hidden reasoning", "thought": True},
                                {
                                    "functionCall": {"id": "call-1", "name": "search", "args": {"q": "A"}},
                                    "thoughtSignature": "opaque-signature",
                                },
                                {"functionCall": {"id": "call-2", "name": "search", "args": {"q": "B"}}},
                            ]
                        }
                    }
                ],
                "usageMetadata": {"promptTokenCount": 3, "candidatesTokenCount": 4, "thoughtsTokenCount": 7},
            },
        )

    provider = GeminiProvider(name="gemini", api_key="test-key", timeout_s=1)
    await provider._client.aclose()
    provider._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        completion = await provider.complete(
            model="gemini-3.8-flash",
            messages=[Message(role=Role.USER, content="search twice")],
            max_tokens=30,
            temperature=0,
            usd_in=0.75,
            usd_out=3.75,
        )
        assert requests[0].headers.get("x-goog-api-key") == "test-key"
        assert "key" not in requests[0].url.params
        assert [call.id for call in completion.tool_calls] == ["call-1", "call-2"]
        assert completion.tool_calls[0].thought_signature == "opaque-signature"
        assert completion.text == ""
        assert completion.usage.output_tokens == 11
        restored = tuple(ProviderToolCall.model_validate_json(call.model_dump_json()) for call in completion.tool_calls)
        await provider.complete(
            model="gemini-3.8-flash",
            messages=[
                Message(role=Role.ASSISTANT, content="", tool_calls=restored),
                Message(role=Role.TOOL, content='{"ok":true,"output":"A"}', name="search", tool_call_id="call-1"),
                Message(role=Role.TOOL, content='{"ok":true,"output":"B"}', name="search", tool_call_id="call-2"),
            ],
            max_tokens=30,
            temperature=0,
            usd_in=0,
            usd_out=0,
        )
        contents = json.loads(requests[1].content)["contents"]
        assert contents[0]["parts"][0] == {
            "functionCall": {"id": "call-1", "name": "search", "args": {"q": "A"}},
            "thoughtSignature": "opaque-signature",
        }
        assert len(contents) == 2
        assert [part["functionResponse"]["id"] for part in contents[1]["parts"]] == ["call-1", "call-2"]
        assert contents[1]["parts"][0]["functionResponse"]["response"] == {"ok": True, "output": "A"}
    finally:
        await provider.close()


async def test_ollama_replays_native_tool_history_with_distinct_call_ids() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "message": {
                    "content": "",
                    "tool_calls": [
                        {"function": {"index": 0, "name": "search", "arguments": {"q": "A"}}},
                        {"function": {"index": 1, "name": "search", "arguments": {"q": "B"}}},
                    ],
                }
            },
        )

    provider = OllamaProvider(host="http://localhost:11434", timeout_s=1)
    await provider._client.aclose()
    provider._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        completion = await provider.complete(
            model="qwen3:4b",
            messages=[
                Message(
                    role=Role.ASSISTANT,
                    content="",
                    tool_calls=(ProviderToolCall(id="local-1", name="search", arguments={"q": "X"}),),
                ),
                Message(role=Role.TOOL, content="found X", name="search", tool_call_id="local-1"),
            ],
            max_tokens=30,
            temperature=0,
            usd_in=0,
            usd_out=0,
        )
        assert captured["messages"][0]["tool_calls"][0]["function"] == {"name": "search", "arguments": {"q": "X"}}
        assert captured["messages"][1]["tool_name"] == "search"
        assert captured["options"]["num_ctx"] == 8192
        assert completion.tool_calls[0].id
        assert completion.tool_calls[0].id != completion.tool_calls[1].id
    finally:
        await provider.close()


@pytest.mark.parametrize("model_present", [True, False])
async def test_ollama_health_requires_the_configured_model(model_present: bool) -> None:
    provider = OllamaProvider(host="http://localhost:11434", timeout_s=1, model="qwen3:4b")
    await provider._client.aclose()
    provider._client = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                json={"models": [{"name": "qwen3:4b"}] if model_present else [{"name": "bge-m3:latest"}]},
            )
        )
    )
    try:
        assert await provider.health() is model_present
    finally:
        await provider.close()


def test_default_catalog_has_offline_and_gemini_tool_routes() -> None:
    registry = ModelRegistry.from_yaml(CONFIG / "models.yaml")
    selector = ModelSelector(CapabilityIndex(registry), HealthMonitor())
    local = selector.select(
        frozenset({Capability.REASONING, Capability.TOOL_CALLING}),
        Constraints(
            cost_policy=CostPolicy.ZERO_COST,
            network_policy=NetworkPolicy.OFFLINE,
            privacy_class=PrivacyClass.SECRET,
        ),
    )
    assert all(spec.provider == "ollama" for spec in local)
    models = [spec.provider_model for spec in local]
    assert "qwen2.5-coder:7b" in models
    assert "qwen3:4b" in models
    assert registry.get("gemini-flash").provider == "gemini"  # type: ignore[union-attr]
    assert registry.get("gemini-flash").cost_class is CostClass.PAID  # type: ignore[union-attr]


def test_selection_excludes_unregistered_providers() -> None:
    registry = ModelRegistry.from_yaml(CONFIG / "models.yaml")
    selector = ModelSelector(CapabilityIndex(registry), HealthMonitor(), providers=ProviderRegistry())
    from atlas.intelligence.errors import RoutingError

    with pytest.raises(RoutingError):
        selector.select(frozenset(), Constraints())


def test_environment_defaults_support_existing_local_models_without_keys() -> None:
    settings = Settings(_env_file=None, OPENROUTER_API_KEY="", ATLAS_EMBED_API_KEY="")  # type: ignore[call-arg]
    assert settings.local_model == "qwen3:4b"
    assert settings.gemini_model == "gemini-3.8-flash"
    assert settings.gemini_billing_tier == "paid"
    assert settings.embed_provider == "ollama"
    assert settings.embed_model == "bge-m3:latest"


def test_validated_image_bytes_are_encoded_as_gemini_inline_data() -> None:
    from atlas.infra.types import ImageInput, ModelRequest

    png = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Wl6vXcAAAAASUVORK5CYII="
    image = ImageInput(mime_type="image/png", data=png)
    request = ModelRequest(correlation_id="vision", prompt="read this", images=(image,))
    message = Message(role=Role.USER, content=request.prompt, images=request.images)
    assert GeminiProvider._encode_contents([message]) == [
        {
            "role": "user",
            "parts": [{"text": "read this"}, {"inlineData": {"mimeType": "image/png", "data": png}}],
        }
    ]
    restored = Message.model_validate_json(message.model_dump_json())
    assert restored.images == (image,)


@pytest.mark.parametrize("data", ["not base64!", "YWJj", ""])
def test_image_contract_rejects_invalid_image_data(data: str) -> None:
    from atlas.infra.types import ImageInput

    with pytest.raises(ValueError):
        ImageInput(mime_type="image/png", data=data)


async def test_gemini_health_checks_model_access_without_generating_content() -> None:
    methods: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        methods.append(request.method)
        assert request.headers.get("x-goog-api-key") == "test-key"
        return httpx.Response(403)

    provider = GeminiProvider(name="gemini", api_key="test-key", timeout_s=1, model="gemini-3.8-flash")
    await provider._client.aclose()
    provider._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        assert await provider.health() is False
        assert methods == ["GET"]
    finally:
        await provider.close()


def test_local_provider_rejects_external_host_for_secret_routing() -> None:
    with pytest.raises(ValueError, match="loopback"):
        OllamaProvider(host="https://example.com", timeout_s=1)


async def test_local_inference_serializes_requests_for_small_memory_hosts() -> None:
    active = 0
    peak = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.01)
        active -= 1
        return httpx.Response(200, json={"message": {"content": "ok"}})

    provider = OllamaProvider(host="http://localhost:11434", timeout_s=1)
    await provider._client.aclose()
    provider._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        await asyncio.gather(
            *(
                provider.complete(
                    model="qwen3:4b",
                    messages=[Message(role=Role.USER, content="hello")],
                    max_tokens=10,
                    temperature=0,
                    usd_in=0,
                    usd_out=0,
                )
                for _ in range(3)
            )
        )
        assert peak == 1
    finally:
        await provider.close()


def test_legacy_request_retains_explicit_policy_fields() -> None:
    from atlas.infra.types import ModelRequest

    request = ModelRequest(
        correlation_id="secret",
        prompt="stay on machine",
        privacy_class=PrivacyClass.SECRET,
        cost_policy=CostPolicy.ZERO_COST,
        network_policy=NetworkPolicy.OFFLINE,
    )
    restored = ModelRequest.model_validate_json(request.model_dump_json())
    assert restored.privacy_class is PrivacyClass.SECRET
    assert restored.cost_policy is CostPolicy.ZERO_COST
    assert restored.network_policy is NetworkPolicy.OFFLINE


@pytest.mark.parametrize("provider_name", ["gemini", "ollama"])
async def test_empty_provider_response_is_an_error_instead_of_a_finished_agent_turn(provider_name: str) -> None:
    provider = (
        GeminiProvider(name="gemini", api_key="test-key", timeout_s=1)
        if provider_name == "gemini"
        else OllamaProvider(host="http://localhost:11434", timeout_s=1)
    )
    await provider._client.aclose()
    provider._client = httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, json={})))
    try:
        with pytest.raises(ProviderError):
            await provider.complete(
                model="model",
                messages=[Message(role=Role.USER, content="go")],
                max_tokens=10,
                temperature=0,
                usd_in=0,
                usd_out=0,
            )
    finally:
        await provider.close()


async def test_malformed_gemini_tool_arguments_raise_a_typed_provider_error() -> None:
    provider = GeminiProvider(name="gemini", api_key="test-key", timeout_s=1)
    await provider._client.aclose()
    provider._client = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                json={
                    "candidates": [{"content": {"parts": [{"functionCall": {"name": "search", "args": "bad"}}]}}],
                },
            )
        )
    )
    try:
        with pytest.raises(ProviderError):
            await provider.complete(
                model="model",
                messages=[Message(role=Role.USER, content="go")],
                max_tokens=10,
                temperature=0,
                usd_in=0,
                usd_out=0,
            )
    finally:
        await provider.close()
