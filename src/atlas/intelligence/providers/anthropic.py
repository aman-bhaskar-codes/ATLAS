"""Anthropic provider adapter."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Sequence
from typing import Any

import httpx

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

    def _payload(
        self, model: str, messages: Sequence[Message], max_tokens: int, temperature: float, stream: bool
    ) -> dict[str, Any]:
        # Anthropic extracts system messages to a top-level parameter
        system_msg = next((m.content for m in messages if m.role == Role.SYSTEM), "")
        chat_msgs = [{"role": m.role.value, "content": m.content} for m in messages if m.role != Role.SYSTEM]
        
        payload: dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "messages": chat_msgs,
            "stream": stream,
        }
        if system_msg:
            payload["system"] = system_msg
        return payload

    async def complete(
        self, *, model: str, messages: Sequence[Message],
        max_tokens: int, temperature: float,
        usd_in: float, usd_out: float,
    ) -> ProviderCompletion:
        try:
            r = await self._client.post(
                f"{self._base}/messages",
                headers={
                    "x-api-key": self._key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json"
                },
                json=self._payload(model, messages, max_tokens, temperature, False),
            )
            if r.status_code == 429:
                raise RateLimitError(f"{self.name} rate limited")
            r.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise ProviderError(f"{self.name} http {exc.response.status_code}") from exc
        except httpx.HTTPError as exc:
            raise ProviderError(f"{self.name} transport: {exc}") from exc
            
        data = r.json()
        text = data["content"][0]["text"]
        u = data.get("usage", {})
        it, ot = int(u.get("input_tokens", 0)), int(u.get("output_tokens", 0))
        usd = it / 1e6 * usd_in + ot / 1e6 * usd_out
        return ProviderCompletion(str(text), Usage(input_tokens=it, output_tokens=ot, usd=usd))

    async def stream(
        self, *, model: str, messages: Sequence[Message],
        max_tokens: int, temperature: float,
    ) -> AsyncIterator[StreamChunk]:
        try:
            async with self._client.stream(
                "POST", f"{self._base}/messages",
                headers={
                    "x-api-key": self._key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json"
                },
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
