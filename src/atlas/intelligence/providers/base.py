"""Provider protocol — the vendor-agnostic seam.

WHY complete + stream + health on every provider: the runtime treats all
providers identically. Adapters own ALL vendor specifics (auth, wire shape,
pricing math) and NOTHING else in the repo imports a vendor SDK.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from typing import Protocol

from atlas.infra.types import ProviderToolCall, ToolCallSpec
from atlas.intelligence.contracts import Message, StreamChunk, Usage


class ProviderCompletion:
    def __init__(
        self,
        text: str,
        usage: Usage,
        tool_calls: tuple[ProviderToolCall, ...] = (),
        reasoning_details: str | None = None,
    ) -> None:
        self.text = text
        self.usage = usage
        self.tool_calls = tool_calls
        self.reasoning_details = reasoning_details


class Provider(Protocol):
    name: str
    is_local: bool

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
    ) -> ProviderCompletion: ...

    def stream(
        self,
        *,
        model: str,
        messages: Sequence[Message],
        max_tokens: int,
        temperature: float,
    ) -> AsyncIterator[StreamChunk]: ...

    async def health(self) -> bool: ...
    async def close(self) -> None: ...
