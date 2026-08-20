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

from atlas.infra.types import ProviderToolCall, ToolCallSpec
from atlas.intelligence.contracts import Message, StreamChunk, Usage
from atlas.intelligence.errors import ProviderError, RateLimitError
from atlas.intelligence.providers.base import ProviderCompletion


class OpenAICompatibleProvider:
    is_local = False

    def __init__(self, *, name: str, base_url: str, api_key: str, timeout_s: float) -> None:
        self.name = name
        self._key = api_key
        self._is_openrouter = api_key.startswith("sk-or-v1-")
        if self._is_openrouter:
            self._base = "https://openrouter.ai/api/v1"
        else:
            self._base = base_url.rstrip("/")
        self._client = httpx.AsyncClient(timeout=timeout_s)

    def _map_model(self, model: str) -> str:
        if not self._is_openrouter:
            return model
        if model == "deepseek-chat":
            return "deepseek/deepseek-chat"
        if model == "glm-5.2":
            return "z-ai/glm-5.2"
        if model == "moonshot-v1-8k":
            return "moonshotai/kimi-k2.7-code"
        return model

    def _payload(
        self,
        model: str,
        messages: Sequence[Message],
        max_tokens: int,
        temperature: float,
        stream: bool,
        tools: Sequence[ToolCallSpec] = (),
    ) -> dict[str, Any]:
        mapped = self._map_model(model)
        payload: dict[str, Any] = {
            "model": mapped,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "stream": stream,
            "messages": [
                {
                    "role": m.role.value,
                    "content": m.content,
                    **({"reasoning_details": m.reasoning_details} if m.reasoning_details else {})
                }
                for m in messages
            ],
        }
        if self._is_openrouter:
            payload["reasoning"] = {"enabled": True}
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
        except httpx.HTTPStatusError as exc:
            raise ProviderError(f"{self.name} http {exc.response.status_code}") from exc
        except httpx.HTTPError as exc:
            raise ProviderError(f"{self.name} transport: {exc}") from exc

        data = r.json()
        text = data["choices"][0]["message"].get("content")
        if text is None:
            import logging

            logging.getLogger("atlas.intel.provider").warning(
                f"{self.name} returned content=None. Raw response: {data}"
            )
            text = ""
        u = data.get("usage", {})
        it, ot = int(u.get("prompt_tokens", 0)), int(u.get("completion_tokens", 0))
        usd = it / 1e6 * usd_in + ot / 1e6 * usd_out
        tool_calls = self._parse_tool_calls(data)
        reasoning_details = data["choices"][0]["message"].get("reasoning_details")
        if isinstance(reasoning_details, list):
            reasoning_details = "\n".join(str(r.get("text", r)) if isinstance(r, dict) else str(r) for r in reasoning_details)
        elif reasoning_details is not None and not isinstance(reasoning_details, str):
            reasoning_details = str(reasoning_details)

        return ProviderCompletion(
            str(text), 
            Usage(input_tokens=it, output_tokens=ot, usd=usd), 
            tool_calls, 
            reasoning_details
        )

    @staticmethod
    def _parse_tool_calls(data: dict[str, Any]) -> tuple[ProviderToolCall, ...]:
        """Normalize OpenAI-format tool_calls into ATLAS form. Invalid
        arguments JSON is skipped (logged) — never crashes the completion."""
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
                import logging

                logging.getLogger("atlas.intel.provider").warning("skipping unparsable tool_call: %r", exc)
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
