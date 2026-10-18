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

    def __init__(self, *, name: str, api_key: str, timeout_s: float, model: str | None = None) -> None:
        self.name = name
        self._key = api_key
        self._model = model
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
                try:
                    response = json.loads(m.content)
                except (ValueError, TypeError):
                    response = {"result": m.content}
                if not isinstance(response, dict):
                    response = {"result": response}
                function_response: dict[str, Any] = {
                    "name": m.name or m.tool_call_id or "tool",
                    "response": response,
                }
                if m.tool_call_id:
                    function_response["id"] = m.tool_call_id
                part = {"functionResponse": function_response}
                # Parallel tool results belong to one user turn.
                if contents and all("functionResponse" in p for p in contents[-1]["parts"]):
                    contents[-1]["parts"].append(part)
                else:
                    contents.append({"role": "user", "parts": [part]})
                continue
            if m.tool_calls:
                parts: list[dict[str, Any]] = []
                if m.content:
                    parts.append({"text": m.content})
                for tc in m.tool_calls:
                    fn: dict[str, Any] = {"name": tc.name, "args": dict(tc.arguments)}
                    if tc.id:
                        fn["id"] = tc.id
                    call_part: dict[str, Any] = {"functionCall": fn}
                    if tc.thought_signature:
                        call_part["thoughtSignature"] = tc.thought_signature
                    parts.append(call_part)
                contents.append({"role": "model", "parts": parts})
                continue
            role = "user" if m.role == Role.USER else "model"
            media_parts: list[dict[str, Any]] = [{"text": m.content}] if m.content else []
            media_parts.extend({"inlineData": {"mimeType": image.mime_type, "data": image.data}} for image in m.images)
            contents.append({"role": role, "parts": media_parts or [{"text": ""}]})
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
            if not self._key:
                raise ProviderError(f"{self.name} API key is not configured")
            r = await self._client.post(
                f"{self._base}/{model}:generateContent",
                headers={"x-goog-api-key": self._key},
                json=self._payload(messages, max_tokens, temperature, tools),
            )
            if r.status_code == 429:
                raise RateLimitError(f"{self.name} rate limited")
            r.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise ProviderError(f"{self.name} http {exc.response.status_code}") from exc
        except httpx.HTTPError as exc:
            raise ProviderError(f"{self.name} transport: {exc}") from exc

        try:
            data = r.json()
        except ValueError as exc:
            raise ProviderError(f"{self.name} returned invalid JSON") from exc
        parts = []
        try:
            parts = data["candidates"][0]["content"]["parts"]
        except (KeyError, IndexError, TypeError):
            parts = []
        text = "".join(p.get("text", "") for p in parts if isinstance(p, dict) and "text" in p and not p.get("thought"))
        try:
            tool_calls = tuple(
                ProviderToolCall(
                    id=str(p["functionCall"].get("id", "")),
                    name=str(p["functionCall"].get("name", "")),
                    arguments=dict(p["functionCall"].get("args") or {}),
                    thought_signature=p.get("thoughtSignature"),
                )
                for p in parts
                if isinstance(p, dict) and isinstance(p.get("functionCall"), dict)
            )
        except (ValueError, TypeError) as exc:
            raise ProviderError(f"{self.name} returned an invalid tool call") from exc
        if not text.strip() and not tool_calls:
            raise ProviderError(f"{self.name} returned no visible response or tool calls")
        u = data.get("usageMetadata", {})
        it = int(u.get("promptTokenCount", 0))
        ot = int(u.get("candidatesTokenCount", 0)) + int(u.get("thoughtsTokenCount", 0))
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
            if not self._key:
                raise ProviderError(f"{self.name} API key is not configured")
            async with self._client.stream(
                "POST",
                f"{self._base}/{model}:streamGenerateContent",
                headers={"x-goog-api-key": self._key},
                params={"alt": "sse"},
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
                        delta = "".join(
                            part.get("text", "")
                            for part in data["candidates"][0]["content"]["parts"]
                            if not part.get("thought")
                        )
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
        if not self._key:
            return False
        endpoint = f"{self._base}/{self._model}" if self._model else self._base
        try:
            response = await self._client.get(endpoint, headers={"x-goog-api-key": self._key}, timeout=2.0)
            if response.status_code != 200:
                return False
            data = response.json()
            models = [data] if self._model else data.get("models", [])
            return any("generateContent" in entry.get("supportedGenerationMethods", []) for entry in models)
        except (httpx.HTTPError, ValueError, TypeError, AttributeError):
            return False

    async def close(self) -> None:
        await self._client.aclose()
