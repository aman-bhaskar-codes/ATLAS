"""Provider protocol — the vendor-agnostic seam.

WHY complete + stream + health on every provider: the runtime treats all
providers identically. Adapters own ALL vendor specifics (auth, wire shape,
pricing math) and NOTHING else in the repo imports a vendor SDK.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from typing import Protocol

from atlas.intelligence.contracts import Message, StreamChunk, Usage


class ProviderCompletion:
    def __init__(self, text: str, usage: Usage) -> None:
        self.text = text
        self.usage = usage


class Provider(Protocol):
    name: str
    is_local: bool

    async def complete(
        self, *, model: str, messages: Sequence[Message],
        max_tokens: int, temperature: float,
        usd_in: float, usd_out: float,
    ) -> ProviderCompletion: ...

    def stream(
        self, *, model: str, messages: Sequence[Message],
        max_tokens: int, temperature: float,
    ) -> AsyncIterator[StreamChunk]: ...

    async def health(self) -> bool: ...
    async def close(self) -> None: ...
