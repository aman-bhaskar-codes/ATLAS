"""Vision grounding (Phase 18) — provider-agnostic.

Input: screenshot + natural-language target. Output: candidate boxes + labels
+ confidence. Vision is a FALLBACK for when structured perception fails — the
system must never assume vision exists.

The protocol is provider-agnostic: an LLM-backed grounder (OpenRouter vision
models) plugs in later behind the same interface; NullVisionGrounder is the
honest default when no vision provider is configured.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from pydantic import BaseModel


class GroundingCandidate(BaseModel):
    model_config = {"frozen": True}
    label: str
    box: tuple[int, int, int, int]  # x, y, w, h
    confidence: float


class GroundingResult(BaseModel):
    model_config = {"frozen": True}
    available: bool
    candidates: tuple[GroundingCandidate, ...] = ()
    note: str = ""


@runtime_checkable
class VisionGrounder(Protocol):
    async def available(self) -> bool: ...

    async def ground(self, image: bytes, query: str) -> GroundingResult: ...


class NullVisionGrounder:
    """Default when no vision provider is configured.

    WHY not raise: callers must degrade gracefully (Phase 45) — structure ->
    accessibility -> DOM -> text -> ask user. A missing grounder is a normal,
    expected state.
    """

    async def available(self) -> bool:
        return False

    async def ground(self, image: bytes, query: str) -> GroundingResult:
        return GroundingResult(available=False, note="vision grounding not configured")
