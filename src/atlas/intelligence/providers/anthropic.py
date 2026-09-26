"""Anthropic provider adapter."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Sequence
from typing import Any

import httpx

from atlas.infra.types import ProviderToolCall, ToolCallSpec
from atlas.intelligence.contracts import Message, Role, StreamChunk, Usage
from atlas.intelligence.errors import ProviderError, RateLimitError
from atlas.intelligence.providers.base import ProviderCompletion


class AnthropicProvider:
    is_local = False

    def __init__(self, *, name: str, api_key: str, timeout_s: float) -> None:
        self.name = name
        self._key = api_key
        self._client = httpx.AsyncClient(timeout=timeout_s)
        self._base = "https://api.anthropic.com/v1"

    @staticmethod
    def _encode_messages(messages: Sequence[Message]) -> list[dict[str, Any]]:
        """Serialize non-system turns into Anthropic message blocks.

        Anthropic has no dedicated tool role: a TOOL turn becomes a ``user``
        message carrying a ``tool_result`` block keyed by ``tool_use_id``, and an
        ASSISTANT turn that chose tools emits ``tool_use`` blocks alongside any
        text. Plain turns keep a simple string content.
        """
        out: list[dict[str, Any]] = []
        for m in messages:
            if m.role == Role.SYSTEM:
                continue
            if m.role == Role.TOOL:
                out.append(
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": m.tool_call_id or "",
                                "content": m.content,
                            }
                        ],
                    }
                )
                continue
            if m.tool_calls:
                blocks: list[dict[str, Any]] = []
                if m.content:
                    blocks.append({"type": "text", "text": m.content})
                blocks.extend(
                    {"type": "tool_use", "id": tc.id or tc.name, "name": tc.name, "input": dict(tc.arguments)}
                    for tc in m.tool_calls
                )
                out.append({"role": "assistant", "content": blocks})
                continue
            out.append({"role": m.role.value, "content": m.content})
        return out

    def _payload(
        self,
        model: str,
        messages: Sequence[Message],
        max_tokens: int,
        temperature: float,
        stream: bool,
        tools: Sequence[ToolCallSpec] = (),
    ) -> dict[str, Any]:
        # Anthropic extracts system messages to a top-level parameter
        system_msg = next((m.content for m in messages if m.role == Role.SYSTEM), "")
        chat_msgs = self._encode_messages(messages)

        payload: dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "messages": chat_msgs,
            "stream": stream,
        }
        if system_msg:
            payload["system"] = system_msg
        if tools:
            payload["tools"] = [
                {
                    "name": t.name,
                    "description": t.description,
                    "input_schema": t.parameters or {"type": "object", "properties": {}},
                }
                for t in tools
            ]
        return payload

    async def complete(
        self,
        *,
        model: str,
        messages: Sequence[Message],
        max_tokens: int,
        temperature: float,
        usd_in: float,
        usd_out: float,
        tools: Sequence[ToolCallSpec] = (),
    ) -> ProviderCompletion:
        try:
            r = await self._client.post(
                f"{self._base}/messages",
                headers={"x-api-key": self._key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
                json=self._payload(model, messages, max_tokens, temperature, False, tools),
            )
            if r.status_code == 429:
                raise RateLimitError(f"{self.name} rate limited")
            r.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise ProviderError(f"{self.name} http {exc.response.status_code}") from exc
        except httpx.HTTPError as exc:
            raise ProviderError(f"{self.name} transport: {exc}") from exc

        data = r.json()
        blocks = data.get("content") or []
        text = "".join(b.get("text", "") for b in blocks if b.get("type") == "text")
        tool_calls = tuple(
            ProviderToolCall(
                id=str(b.get("id", "")),
                name=str(b.get("name", "")),
                arguments=dict(b.get("input") or {}),
            )
            for b in blocks
            if b.get("type") == "tool_use"
        )
        u = data.get("usage", {})
        it, ot = int(u.get("input_tokens", 0)), int(u.get("output_tokens", 0))
        usd = it / 1e6 * usd_in + ot / 1e6 * usd_out
        return ProviderCompletion(str(text), Usage(input_tokens=it, output_tokens=ot, usd=usd), tool_calls)

    async def stream(
        self,
        *,
        model: str,
        messages: Sequence[Message],
        max_tokens: int,
        temperature: float,
    ) -> AsyncIterator[StreamChunk]:
        try:
            async with self._client.stream(
                "POST",
                f"{self._base}/messages",
                headers={"x-api-key": self._key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
                json=self._payload(model, messages, max_tokens, temperature, True),
            ) as r:
                if r.status_code == 429:
                    raise RateLimitError(f"{self.name} rate limited")
                r.raise_for_status()
                async for line in r.aiter_lines():
                    if not line.startswith("data: "):
                        continue
                    body = line[6:]
                    try:
                        data = json.loads(body)
                    except json.JSONDecodeError:
                        continue

                    typ = data.get("type")
                    if typ == "content_block_delta":
                        delta = data.get("delta", {}).get("text", "")
                        if delta:
                            yield StreamChunk(delta=delta, done=False)
                    elif typ == "message_stop":
                        yield StreamChunk(delta="", done=True)
                        return
        except httpx.HTTPStatusError as exc:
            raise ProviderError(f"{self.name} stream http {exc.response.status_code}") from exc
        except httpx.HTTPError as exc:
            raise ProviderError(f"{self.name} stream transport: {exc}") from exc

    async def health(self) -> bool:
        return bool(self._key)

    async def close(self) -> None:
        await self._client.aclose()
