"""Gemini provider adapter."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Sequence
from typing import Any

import httpx

from atlas.infra.types import ProviderToolCall, ToolCallSpec
from atlas.intelligence.contracts import Message, Role, StreamChunk, Usage
from atlas.intelligence.errors import ProviderError, RateLimitError
from atlas.intelligence.providers.base import ProviderCompletion


class GeminiProvider:
    is_local = False

    def __init__(self, *, name: str, api_key: str, timeout_s: float) -> None:
        self.name = name
        self._key = api_key
        self._client = httpx.AsyncClient(timeout=timeout_s)
        self._base = "https://generativelanguage.googleapis.com/v1beta/models"

    @staticmethod
    def _encode_contents(messages: Sequence[Message]) -> list[dict[str, Any]]:
        """Serialize non-system turns into Gemini ``contents``.

        Gemini uses ``functionCall`` parts on ``model`` turns and
        ``functionResponse`` parts on ``user`` turns (it has no distinct tool
        role). A TOOL turn is emitted as a functionResponse; an ASSISTANT turn
        that chose tools emits functionCall parts alongside any text.
        """
        contents: list[dict[str, Any]] = []
        for m in messages:
            if m.role == Role.SYSTEM:
                continue
            if m.role == Role.TOOL:
                contents.append(
                    {
                        "role": "user",
                        "parts": [
                            {
                                "functionResponse": {
                                    "name": m.name or m.tool_call_id or "tool",
                                    "response": {"result": m.content},
                                }
                            }
                        ],
                    }
                )
                continue
            if m.tool_calls:
                parts: list[dict[str, Any]] = []
                if m.content:
                    parts.append({"text": m.content})
                parts.extend({"functionCall": {"name": tc.name, "args": dict(tc.arguments)}} for tc in m.tool_calls)
                contents.append({"role": "model", "parts": parts})
                continue
            role = "user" if m.role == Role.USER else "model"
            contents.append({"role": role, "parts": [{"text": m.content}]})
        return contents

    def _payload(
        self,
        messages: Sequence[Message],
        max_tokens: int,
        temperature: float,
        tools: Sequence[ToolCallSpec] = (),
    ) -> dict[str, Any]:
        system_msg = next((m.content for m in messages if m.role == Role.SYSTEM), "")
        payload: dict[str, Any] = {
            "contents": self._encode_contents(messages),
            "generationConfig": {
                "temperature": temperature,
                "maxOutputTokens": max_tokens,
            },
        }
        if system_msg:
            payload["systemInstruction"] = {"parts": [{"text": system_msg}]}
        if tools:
            payload["tools"] = [
                {
                    "functionDeclarations": [
                        {
                            "name": t.name,
                            "description": t.description,
                            "parameters": t.parameters or {"type": "object", "properties": {}},
                        }
                        for t in tools
                    ]
                }
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
                f"{self._base}/{model}:generateContent",
                params={"key": self._key},
                json=self._payload(messages, max_tokens, temperature, tools),
            )
            if r.status_code == 429:
                raise RateLimitError(f"{self.name} rate limited")
            r.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise ProviderError(f"{self.name} http {exc.response.status_code}") from exc
        except httpx.HTTPError as exc:
            raise ProviderError(f"{self.name} transport: {exc}") from exc

        data = r.json()
        parts = []
        try:
            parts = data["candidates"][0]["content"]["parts"]
        except (KeyError, IndexError):
            parts = []
        text = "".join(p.get("text", "") for p in parts if isinstance(p, dict) and "text" in p)
        tool_calls = tuple(
            ProviderToolCall(
                id=str(p["functionCall"].get("name", "")),
                name=str(p["functionCall"].get("name", "")),
                arguments=dict(p["functionCall"].get("args") or {}),
            )
            for p in parts
            if isinstance(p, dict) and isinstance(p.get("functionCall"), dict)
        )
        u = data.get("usageMetadata", {})
        it, ot = int(u.get("promptTokenCount", 0)), int(u.get("candidatesTokenCount", 0))
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
                f"{self._base}/{model}:streamGenerateContent",
                params={"key": self._key, "alt": "sse"},
                json=self._payload(messages, max_tokens, temperature),
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

                    try:
                        delta = data["candidates"][0]["content"]["parts"][0]["text"]
                        if delta:
                            yield StreamChunk(delta=delta, done=False)
                    except (KeyError, IndexError):
                        pass

                yield StreamChunk(delta="", done=True)
        except httpx.HTTPStatusError as exc:
            raise ProviderError(f"{self.name} stream http {exc.response.status_code}") from exc
        except httpx.HTTPError as exc:
            raise ProviderError(f"{self.name} stream transport: {exc}") from exc

    async def health(self) -> bool:
        return bool(self._key)

    async def close(self) -> None:
        await self._client.aclose()
