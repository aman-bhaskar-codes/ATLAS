"""M0.1 — provider-native tool-calling round-trips.

Each provider must (a) serialize a multi-turn tool loop into its own wire shape
and (b) parse the model's tool choice back into ATLAS's ProviderToolCall. We
drive real adapters through an httpx.MockTransport so nothing hits the network:
the handler captures the outgoing payload (serialization) and returns a canned
tool-call response (parsing).
"""

from __future__ import annotations

import json
from typing import Any

import httpx

from atlas.infra.types import ProviderToolCall, ToolCallSpec
from atlas.intelligence.contracts import Message, Role
from atlas.intelligence.providers.anthropic import AnthropicProvider
from atlas.intelligence.providers.gemini import GeminiProvider
from atlas.intelligence.providers.openai_compatible import OpenAICompatibleProvider

# A tool loop: user asks -> assistant chose a tool -> tool result comes back.
TOOL = ToolCallSpec(name="search", description="search the web", parameters={"type": "object"})
LOOP = [
    Message(role=Role.USER, content="find X"),
    Message(
        role=Role.ASSISTANT,
        content="",
        tool_calls=(ProviderToolCall(id="call_1", name="search", arguments={"q": "X"}),),
    ),
    Message(role=Role.TOOL, content="found: X is 42", tool_call_id="call_1", name="search"),
]


def _mock(provider: Any, response_json: dict[str, Any], captured: dict[str, Any]) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        captured["payload"] = json.loads(request.content)
        return httpx.Response(200, json=response_json)

    provider._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_openai_compatible_round_trip() -> None:
    p = OpenAICompatibleProvider(name="or", base_url="https://x/v1", api_key="k", timeout_s=5)
    captured: dict[str, Any] = {}
    _mock(
        p,
        {
            "choices": [
                {
                    "message": {
                        "content": "",
                        "tool_calls": [
                            {"id": "c2", "type": "function", "function": {"name": "search", "arguments": '{"q":"Y"}'}}
                        ],
                    }
                }
            ],
            "usage": {"prompt_tokens": 3, "completion_tokens": 4},
        },
        captured,
    )
    comp = await p.complete(
        model="glm-5.2", messages=LOOP, max_tokens=64, temperature=0.0, usd_in=0.0, usd_out=0.0, tools=[TOOL]
    )
    # parsing
    assert comp.tool_calls == (ProviderToolCall(id="c2", name="search", arguments={"q": "Y"}),)
    # serialization: tools advertised, assistant tool_calls + tool-result turn present
    msgs = captured["payload"]["messages"]
    assert captured["payload"]["tools"][0]["function"]["name"] == "search"
    assert any(m["role"] == "assistant" and m.get("tool_calls") for m in msgs)
    tool_turn = next(m for m in msgs if m["role"] == "tool")
    assert tool_turn["tool_call_id"] == "call_1"
    await p.close()


async def test_anthropic_round_trip() -> None:
    p = AnthropicProvider(name="anthropic", api_key="k", timeout_s=5)
    captured: dict[str, Any] = {}
    _mock(
        p,
        {
            "content": [{"type": "tool_use", "id": "tu9", "name": "search", "input": {"q": "Y"}}],
            "usage": {"input_tokens": 3, "output_tokens": 4},
        },
        captured,
    )
    comp = await p.complete(
        model="claude", messages=LOOP, max_tokens=64, temperature=0.0, usd_in=0.0, usd_out=0.0, tools=[TOOL]
    )
    assert comp.tool_calls == (ProviderToolCall(id="tu9", name="search", arguments={"q": "Y"}),)
    msgs = captured["payload"]["messages"]
    # assistant tool_use block
    asst = next(m for m in msgs if m["role"] == "assistant" and isinstance(m["content"], list))
    assert any(b["type"] == "tool_use" and b["id"] == "call_1" for b in asst["content"])
    # tool result routed as a user tool_result block
    result = next(m for m in msgs if isinstance(m["content"], list) and m["content"][0].get("type") == "tool_result")
    assert result["content"][0]["tool_use_id"] == "call_1"
    await p.close()


async def test_gemini_round_trip() -> None:
    p = GeminiProvider(name="gemini", api_key="k", timeout_s=5)
    captured: dict[str, Any] = {}
    _mock(
        p,
        {
            "candidates": [{"content": {"parts": [{"functionCall": {"name": "search", "args": {"q": "Y"}}}]}}],
            "usageMetadata": {"promptTokenCount": 3, "candidatesTokenCount": 4},
        },
        captured,
    )
    comp = await p.complete(
        model="gemini-2.0", messages=LOOP, max_tokens=64, temperature=0.0, usd_in=0.0, usd_out=0.0, tools=[TOOL]
    )
    assert comp.tool_calls == (ProviderToolCall(id="search", name="search", arguments={"q": "Y"}),)
    payload = captured["payload"]
    assert payload["tools"][0]["functionDeclarations"][0]["name"] == "search"
    contents = payload["contents"]
    assert any("functionCall" in part for c in contents for part in c["parts"])
    assert any("functionResponse" in part for c in contents for part in c["parts"])
    await p.close()


async def test_plain_messages_unaffected() -> None:
    """Backward-compat: a normal chat with no tools serializes as before."""
    p = OpenAICompatibleProvider(name="or", base_url="https://x/v1", api_key="k", timeout_s=5)
    captured: dict[str, Any] = {}
    _mock(p, {"choices": [{"message": {"content": "hi"}}], "usage": {}}, captured)
    comp = await p.complete(
        model="glm-5.2",
        messages=[Message(role=Role.USER, content="hello")],
        max_tokens=8,
        temperature=0.0,
        usd_in=0.0,
        usd_out=0.0,
    )
    assert comp.text == "hi"
    assert comp.tool_calls == ()
    assert "tools" not in captured["payload"]
    assert captured["payload"]["messages"] == [{"role": "user", "content": "hello"}]
    await p.close()
