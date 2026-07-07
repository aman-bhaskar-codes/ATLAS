"""OpenAI-compatible provider — covers OpenAI, DeepSeek, GLM, Kimi, OpenRouter.

WHY one adapter for five vendors: they all speak /chat/completions. One tested
adapter parametrized by (base_url, api_key) is far less surface than five copies.
Each vendor is still a distinct REGISTERED provider (own key, own base_url) so
health/rate-limit/circuit state is tracked per-vendor.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Sequence
from typing import Any

import httpx

from atlas.intelligence.contracts import Message, StreamChunk, Usage
from atlas.intelligence.errors import ProviderError, RateLimitError
from atlas.intelligence.providers.base import ProviderCompletion


class OpenAICompatibleProvider:
    is_local = False

    def __init__(self, *, name: str, base_url: str, api_key: str, timeout_s: float) -> None:
        self.name = name
        self._base = base_url.rstrip("/")
        self._key = api_key
        self._client = httpx.AsyncClient(timeout=timeout_s)

    def _payload(
        self, model: str, messages: Sequence[Message], max_tokens: int, temperature: float, stream: bool
    ) -> dict[str, Any]:
        return {
            "model": model, "max_tokens": max_tokens, "temperature": temperature,
            "stream": stream,
            "messages": [{"role": m.role.value, "content": m.content} for m in messages],
        }

    async def complete(
        self, *, model: str, messages: Sequence[Message],
        max_tokens: int, temperature: float,
        usd_in: float, usd_out: float,
    ) -> ProviderCompletion:
        try:
            r = await self._client.post(
                f"{self._base}/chat/completions",
                headers={"Authorization": f"Bearer {self._key}"},
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
        text = data["choices"][0]["message"]["content"]
        u = data.get("usage", {})
        it, ot = int(u.get("prompt_tokens", 0)), int(u.get("completion_tokens", 0))
        usd = it / 1e6 * usd_in + ot / 1e6 * usd_out
        return ProviderCompletion(str(text), Usage(input_tokens=it, output_tokens=ot, usd=usd))

    async def stream(
        self, *, model: str, messages: Sequence[Message],
        max_tokens: int, temperature: float,
    ) -> AsyncIterator[StreamChunk]:
        try:
            async with self._client.stream(
                "POST", f"{self._base}/chat/completions",
                headers={"Authorization": f"Bearer {self._key}"},
                json=self._payload(model, messages, max_tokens, temperature, True),
            ) as r:
                if r.status_code == 429:
                    raise RateLimitError(f"{self.name} rate limited")
                r.raise_for_status()
                async for line in r.aiter_lines():
                    if not line.startswith("data: "):
                        continue
                    body = line[6:]
                    if body.strip() == "[DONE]":
                        yield StreamChunk(delta="", done=True)
                        return
                    try:
                        delta = json.loads(body)["choices"][0]["delta"].get("content", "")
                    except (json.JSONDecodeError, KeyError, IndexError):
                        continue
                    if delta:
                        yield StreamChunk(delta=delta)
        except httpx.HTTPStatusError as exc:
            raise ProviderError(f"{self.name} stream http {exc.response.status_code}") from exc
        except httpx.HTTPError as exc:
            raise ProviderError(f"{self.name} stream transport: {exc}") from exc

    async def health(self) -> bool:
        return bool(self._key)

    async def close(self) -> None:
        await self._client.aclose()
