"""Groq provider adapter — free-tier fast inference.

WHY a separate class when Groq speaks OpenAI-compatible: Groq sends rate-limit
state in response headers (x-ratelimit-remaining-requests, etc.) which we parse
and feed back into the FreeQuotaGovernor. The OpenAICompatibleProvider doesn't
do this. Everything else delegates to the base class.

Free-tier limits (as of 2025, subject to change):
  llama-3.3-70b-versatile: 1000 req/day, 100K tokens/day, 30 RPM
  llama-3.1-8b-instant:    14400 req/day, 500K tokens/day, 30 RPM
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Sequence
from typing import Any

import httpx

from atlas.infra.logging import get_logger
from atlas.infra.types import ProviderToolCall, ToolCallSpec
from atlas.intelligence.contracts import Message, StreamChunk, Usage
from atlas.intelligence.errors import ProviderError, RateLimitError
from atlas.intelligence.providers.base import ProviderCompletion

_log = get_logger("atlas.intel.provider.groq")


class GroqProvider:
    """Groq cloud inference — OpenAI-compatible API with rate-limit header parsing."""

    is_local = False

    def __init__(self, *, name: str, api_key: str, timeout_s: float) -> None:
        self.name = name
        self._key = api_key
        self._base = "https://api.groq.com/openai/v1"
        self._client = httpx.AsyncClient(timeout=timeout_s)
        # Rate-limit state parsed from response headers
        self.rate_limit_remaining: int | None = None
        self.rate_limit_tokens_remaining: int | None = None

    def _payload(
        self,
        model: str,
        messages: Sequence[Message],
        max_tokens: int,
        temperature: float,
        stream: bool,
        tools: Sequence[ToolCallSpec] = (),
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "stream": stream,
            "messages": [{"role": m.role.value, "content": m.content} for m in messages],
        }
        if tools:
            payload["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": t.name,
                        "description": t.description,
                        "parameters": t.parameters or {"type": "object", "properties": {}},
                    },
                }
                for t in tools
            ]
            payload["tool_choice"] = "auto"
        return payload

    def _parse_rate_limit_headers(self, headers: httpx.Headers) -> None:
        """Extract Groq rate-limit headers for quota governor feedback."""
        for key, attr in [
            ("x-ratelimit-remaining-requests", "rate_limit_remaining"),
            ("x-ratelimit-remaining-tokens", "rate_limit_tokens_remaining"),
        ]:
            val = headers.get(key)
            if val is not None:
                try:
                    setattr(self, attr, int(val))
                except ValueError:
                    pass

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
                f"{self._base}/chat/completions",
                headers={"Authorization": f"Bearer {self._key}"},
                json=self._payload(model, messages, max_tokens, temperature, False, tools),
            )
            if r.status_code == 429:
                raise RateLimitError(f"{self.name} rate limited")
            r.raise_for_status()
            self._parse_rate_limit_headers(r.headers)
        except httpx.HTTPStatusError as exc:
            raise ProviderError(f"{self.name} http {exc.response.status_code}") from exc
        except httpx.HTTPError as exc:
            raise ProviderError(f"{self.name} transport: {exc}") from exc

        data = r.json()
        text = data["choices"][0]["message"].get("content")
        if text is None:
            _log.warning(f"{self.name} returned content=None. Raw: {data}")
            text = ""
        u = data.get("usage", {})
        it, ot = int(u.get("prompt_tokens", 0)), int(u.get("completion_tokens", 0))
        usd = it / 1e6 * usd_in + ot / 1e6 * usd_out
        tool_calls = self._parse_tool_calls(data)
        return ProviderCompletion(str(text), Usage(input_tokens=it, output_tokens=ot, usd=usd), tool_calls)

    @staticmethod
    def _parse_tool_calls(data: dict[str, Any]) -> tuple[ProviderToolCall, ...]:
        raw_calls = (data.get("choices") or [{}])[0].get("message", {}).get("tool_calls") or []
        calls: list[ProviderToolCall] = []
        for tc in raw_calls:
            try:
                fn = tc["function"]
                args = json.loads(fn.get("arguments") or "{}")
                if not isinstance(args, dict):
                    continue
                calls.append(ProviderToolCall(id=str(tc.get("id", "")), name=str(fn["name"]), arguments=args))
            except (KeyError, json.JSONDecodeError, TypeError) as exc:
                _log.warning("skipping unparsable tool_call: %r", exc)
        return tuple(calls)

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
                f"{self._base}/chat/completions",
                headers={"Authorization": f"Bearer {self._key}"},
                json=self._payload(model, messages, max_tokens, temperature, True),
            ) as r:
                if r.status_code == 429:
                    raise RateLimitError(f"{self.name} rate limited")
                r.raise_for_status()
                self._parse_rate_limit_headers(r.headers)
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
