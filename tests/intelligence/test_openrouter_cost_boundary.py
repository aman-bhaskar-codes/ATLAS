"""Verify provider-enforced zero cost and reported billing, independent of YAML."""

import json

import httpx
import pytest

from atlas.intelligence.contracts import Message, Role
from atlas.intelligence.providers.openai_compatible import OpenAICompatibleProvider


@pytest.mark.parametrize("stream", [False, True])
async def test_zero_cost_openrouter_requests_have_provider_price_ceiling(stream: bool) -> None:
    received: list[dict[str, object]] = []

    def respond(request: httpx.Request) -> httpx.Response:
        received.append(json.loads(request.content))
        if stream:
            return httpx.Response(200, text='data: {"choices":[{"delta":{"content":"hello"}}]}\n\ndata: [DONE]\n\n')
        return httpx.Response(200, json={"choices": [{"message": {"content": "hello"}}], "usage": {}})

    # The key is synthetic and deliberately lacks the old sk-or-v1 prefix.
    provider = OpenAICompatibleProvider(
        name="openrouter", base_url="https://openrouter.ai/api/v1", api_key="test-only", timeout_s=1, zero_cost=True
    )
    await provider._client.aclose()
    provider._client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
    try:
        messages = [Message(role=Role.USER, content="hello")]
        if stream:
            async for _ in provider.stream(model="provider/model", messages=messages, max_tokens=8, temperature=0):
                pass
        else:
            await provider.complete(
                model="provider/model", messages=messages, max_tokens=8, temperature=0, usd_in=0, usd_out=0
            )
        assert received[0].get("provider") == {"max_price": {"prompt": 0, "completion": 0}}
    finally:
        await provider.close()


async def test_actual_billed_cost_overrides_stale_catalog_prices() -> None:
    provider = OpenAICompatibleProvider(
        name="openrouter", base_url="https://openrouter.ai/api/v1", api_key="test-only", timeout_s=1
    )
    await provider._client.aclose()
    provider._client = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                200,
                json={
                    "choices": [{"message": {"content": "hello"}}],
                    "usage": {"prompt_tokens": 100, "completion_tokens": 10, "cost": 0.25},
                },
            )
        )
    )
    try:
        result = await provider.complete(
            model="provider/model", messages=[Message(role=Role.USER, content="hello")],
            max_tokens=8, temperature=0, usd_in=0, usd_out=0,
        )
        assert result.usage.usd == 0.25
    finally:
        await provider.close()
