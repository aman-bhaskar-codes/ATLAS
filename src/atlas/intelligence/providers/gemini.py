"""Gemini provider adapter."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Sequence
from typing import Any

import httpx

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

    def _payload(self, messages: Sequence[Message], max_tokens: int, temperature: float) -> dict[str, Any]:
        system_msg = next((m.content for m in messages if m.role == Role.SYSTEM), "")
        chat_msgs = []
        for m in messages:
            if m.role == Role.SYSTEM:
                continue
            role = "user" if m.role == Role.USER else "model"
            chat_msgs.append({"role": role, "parts": [{"text": m.content}]})
            
        payload: dict[str, Any] = {
            "contents": chat_msgs,
            "generationConfig": {
                "temperature": temperature,
                "maxOutputTokens": max_tokens,
            }
        }
        if system_msg:
            payload["systemInstruction"] = {"parts": [{"text": system_msg}]}
        return payload

    async def complete(
        self, *, model: str, messages: Sequence[Message],
        max_tokens: int, temperature: float,
        usd_in: float, usd_out: float,
    ) -> ProviderCompletion:
        try:
            r = await self._client.post(
                f"{self._base}/{model}:generateContent",
                params={"key": self._key},
                json=self._payload(messages, max_tokens, temperature),
            )
            if r.status_code == 429:
                raise RateLimitError(f"{self.name} rate limited")
            r.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise ProviderError(f"{self.name} http {exc.response.status_code}") from exc
        except httpx.HTTPError as exc:
            raise ProviderError(f"{self.name} transport: {exc}") from exc
            
        data = r.json()
        try:
            text = data["candidates"][0]["content"]["parts"][0]["text"]
        except (KeyError, IndexError):
            text = ""
        u = data.get("usageMetadata", {})
        it, ot = int(u.get("promptTokenCount", 0)), int(u.get("candidatesTokenCount", 0))
        usd = it / 1e6 * usd_in + ot / 1e6 * usd_out
        return ProviderCompletion(str(text), Usage(input_tokens=it, output_tokens=ot, usd=usd))

    async def stream(
        self, *, model: str, messages: Sequence[Message],
        max_tokens: int, temperature: float,
    ) -> AsyncIterator[StreamChunk]:
        try:
            async with self._client.stream(
                "POST", f"{self._base}/{model}:streamGenerateContent",
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
