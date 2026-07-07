"""Reflection hook — the Phase 4.5 self-critique seam.

WHY a no-op default now: the loop calls critique() before consequential actions;
in Phase 4.5 a real hook (cheap local critique) will revise or abort a risky
action. It sits BEFORE the Safety Engine and can only make an action safer
(revise/abort), never grant privilege. Shipping the seam now = no loop rewrite
later.
"""

from __future__ import annotations

from typing import Protocol

from atlas.orchestration.types import Action


class ReflectionHook(Protocol):
    async def critique(self, action: Action, context: str) -> Action: ...


class NoOpReflection:
    async def critique(self, action: Action, context: str) -> Action:
        return action
