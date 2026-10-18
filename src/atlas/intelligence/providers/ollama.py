"""Ollama local provider adapter."""

from __future__ import annotations

import asyncio
import ipaddress
import json
from collections.abc import AsyncIterator, Sequence
from typing import Any
from urllib.parse import urlsplit
from uuid import uuid4

import httpx

from atlas.infra.types import ProviderToolCall, ToolCallSpec
from atlas.intelligence.contracts import Message, Role, StreamChunk, Usage
from atlas.intelligence.errors import ProviderError
from atlas.intelligence.providers.base import ProviderCompletion


class OllamaProvider:
    is_local = True

    def __init__(
        self,
        host: str,
        timeout_s: float,
        *,
        model: str | None = None,
        context_length: int = 8192,
    ) -> None:
        url = urlsplit(host)
        hostname = url.hostname or ""
        try:
            loopback = ipaddress.ip_address(hostname).is_loopback
        except ValueError:
            loopback = hostname.lower() == "localhost"
        if (
            not loopback
            or url.scheme not in {"http", "https"}
            or url.username
            or url.password
            or url.query
            or url.fragment
            or url.path not in {"", "/"}
        ):
            raise ValueError("local Ollama must use a loopback HTTP(S) host without credentials or a path")
        self.name = "ollama"
        self._host = host.rstrip("/")
        self._model = model
        self._context_length = context_length
        # Environment proxies must never move local/SECRET inputs off machine.
        self._client = httpx.AsyncClient(timeout=timeout_s, trust_env=False)
        self._sem = asyncio.Semaphore(1)

    @staticmethod
    def _encode_messages(messages: Sequence[Message]) -> list[dict[str, Any]]:
        encoded: list[dict[str, Any]] = []
        for message in messages:
            turn: dict[str, Any] = {"role": message.role.value, "content": message.content}
            if message.tool_calls:
                turn["tool_calls"] = [
                    {"function": {"name": call.name, "arguments": dict(call.arguments)}} for call in message.tool_calls
                ]
            if message.role is Role.TOOL:
                turn["tool_name"] = message.name or message.tool_call_id or "tool"
            if message.images:
                turn["images"] = [image.data for image in message.images]
            encoded.append(turn)
        return encoded

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
            "messages": self._encode_messages(messages),
            "stream": stream,
            # WHY think=False: qwen3 is a thinking model that spends all token budget
            # on internal reasoning, returning empty content. Disable thinking mode
            # unless explicitly asked for deep reasoning.
            "think": False,
            "options": {
                "num_predict": max_tokens,
                "temperature": temperature,
                "num_ctx": self._context_length,
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
            async with self._sem:
                r = await self._client.post(
                    f"{self._host}/api/chat",
                    json=self._payload(model, messages, max_tokens, temperature, False, tools),
                )
            r.raise_for_status()
        except httpx.HTTPError as exc:
            raise ProviderError(f"{self.name} transport: {exc}") from exc

        try:
            data = r.json()
        except ValueError as exc:
            raise ProviderError(f"{self.name} returned invalid JSON") from exc
        text = data.get("message", {}).get("content", "")
        it, ot = int(data.get("prompt_eval_count", 0)), int(data.get("eval_count", 0))
        tool_calls = self._parse_tool_calls(data)
        if not str(text or "").strip() and not tool_calls:
            raise ProviderError(f"{self.name} returned no visible response or tool calls")
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
                calls.append(
                    ProviderToolCall(id=str(tc.get("id") or uuid4().hex), name=str(fn["name"]), arguments=args)
                )
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
            async with self._sem:
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
            r = await self._client.get(f"{self._host}/api/tags", timeout=2.0)
            if r.status_code != 200:
                return False
            models = r.json().get("models", [])
            if self._model is None:
                return bool(models)
            target = self._model if ":" in self._model else f"{self._model}:latest"
            return any(entry.get("name") == target or entry.get("model") == target for entry in models)
        except (httpx.HTTPError, ValueError, TypeError, AttributeError):
            return False

    async def close(self) -> None:
        await self._client.aclose()
