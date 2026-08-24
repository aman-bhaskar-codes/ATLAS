"""Ollama local provider adapter."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Sequence
from typing import Any

import httpx

from atlas.infra.types import ProviderToolCall, ToolCallSpec
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
            "messages": [{"role": m.role.value, "content": m.content} for m in messages],
            "stream": stream,
            # WHY think=False: qwen3 is a thinking model that spends all token budget
            # on internal reasoning, returning empty content. Disable thinking mode
            # unless explicitly asked for deep reasoning.
            "think": False,
            "options": {
                "num_predict": max_tokens,
                "temperature": temperature,
            },
        }

        # WHY format=json: qwen3:4b without this writes thousands of tokens of
        # verbose chain-of-thought BEFORE the JSON, consuming the entire token
        # budget. Ollama's native JSON mode forces the model to output ONLY valid
        # JSON, which is exactly what the understanding/planner/critique pipelines
        # need. We detect JSON-expecting prompts by checking the system message.
        if not tools:
            system_text = ""
            for m in messages:
                if m.role.value == "system":
                    system_text += m.content
            if "JSON" in system_text or "json" in system_text:
                payload["format"] = "json"

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
                f"{self._host}/api/chat",
                json=self._payload(model, messages, max_tokens, temperature, False, tools),
            )
            r.raise_for_status()
        except httpx.HTTPError as exc:
            raise ProviderError(f"{self.name} transport: {exc}") from exc

        data = r.json()
        text = data.get("message", {}).get("content", "")
        if not text:
            import logging
            thinking = data.get("message", {}).get("thinking", "")
            logging.getLogger("atlas.intel.ollama").warning(
                "ollama returned empty content. done_reason=%s thinking_len=%d",
                data.get("done_reason"),
                len(thinking or ""),
            )
        it, ot = int(data.get("prompt_eval_count", 0)), int(data.get("eval_count", 0))
        tool_calls = self._parse_tool_calls(data)
        # ollama is free
        return ProviderCompletion(str(text), Usage(input_tokens=it, output_tokens=ot, usd=0.0), tool_calls)

    @staticmethod
    def _parse_tool_calls(data: dict[str, Any]) -> tuple[ProviderToolCall, ...]:
        """Ollama returns tool_calls on the message object in OpenAI-like form."""
        raw_calls = data.get("message", {}).get("tool_calls") or []
        calls: list[ProviderToolCall] = []
        for tc in raw_calls:
            try:
                fn = tc["function"]
                args = fn.get("arguments") or {}
                if isinstance(args, str):
                    args = json.loads(args)
                if not isinstance(args, dict):
                    continue
                calls.append(ProviderToolCall(id=str(tc.get("id", "")), name=str(fn["name"]), arguments=args))
            except (KeyError, json.JSONDecodeError, TypeError):
                continue
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
                f"{self._host}/api/chat",
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
