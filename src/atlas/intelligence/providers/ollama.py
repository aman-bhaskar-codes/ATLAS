"""Ollama local provider adapter."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Sequence
from typing import Any

import httpx

from atlas.intelligence.contracts import Message, StreamChunk, Usage
from atlas.intelligence.errors import ProviderError
from atlas.intelligence.providers.base import ProviderCompletion


class OllamaProvider:
    is_local = True

    def __init__(self, host: str, timeout_s: float) -> None:
        self.name = "ollama"
        self._host = host.rstrip("/")
        self._client = httpx.AsyncClient(timeout=timeout_s)

    def _payload(
        self, model: str, messages: Sequence[Message], max_tokens: int, temperature: float, stream: bool
    ) -> dict[str, Any]:
        return {
            "model": model,
            "messages": [{"role": m.role.value, "content": m.content} for m in messages],
            "stream": stream,
            "options": {
                "num_predict": max_tokens,
                "temperature": temperature,
            }
        }

    async def complete(
        self, *, model: str, messages: Sequence[Message],
        max_tokens: int, temperature: float,
        usd_in: float, usd_out: float,
    ) -> ProviderCompletion:
        try:
            r = await self._client.post(
                f"{self._host}/api/chat",
                json=self._payload(model, messages, max_tokens, temperature, False),
            )
            r.raise_for_status()
        except httpx.HTTPError as exc:
            raise ProviderError(f"{self.name} transport: {exc}") from exc
            
        data = r.json()
        text = data.get("message", {}).get("content", "")
        it, ot = int(data.get("prompt_eval_count", 0)), int(data.get("eval_count", 0))
        # ollama is free
        return ProviderCompletion(str(text), Usage(input_tokens=it, output_tokens=ot, usd=0.0))

    async def stream(
        self, *, model: str, messages: Sequence[Message],
        max_tokens: int, temperature: float,
    ) -> AsyncIterator[StreamChunk]:
        try:
            async with self._client.stream(
                "POST", f"{self._host}/api/chat",
                json=self._payload(model, messages, max_tokens, temperature, True),
            ) as r:
                r.raise_for_status()
                async for line in r.aiter_lines():
                    if not line:
                        continue
                    try:
                        data = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                        
                    delta = data.get("message", {}).get("content", "")
                    done = data.get("done", False)
                    if delta or done:
                        yield StreamChunk(delta=delta, done=done)
        except httpx.HTTPError as exc:
            raise ProviderError(f"{self.name} stream transport: {exc}") from exc

    async def health(self) -> bool:
        try:
            r = await self._client.get(f"{self._host}/api/version", timeout=2.0)
            return r.status_code == 200
        except httpx.HTTPError:
            return False

    async def close(self) -> None:
        await self._client.aclose()
