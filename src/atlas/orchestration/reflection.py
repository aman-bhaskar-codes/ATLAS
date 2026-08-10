"""Reflection — the R in OTAR (Observe → Think → Act → Reflect).

WHY: After every action, the agent should reflect on what happened. Did the
action succeed? What did we learn? Should we adjust the plan? This is Vamos's
explicit Reflect step. The cheap local-model call catches mistakes early and
records learnings to working memory, preventing the agent from repeating
failures.

The ReflectionHook protocol has two entry points:
  - critique(action, context): PRE-action review (Phase 4.5 self-critique)
  - reflect(action, observation, context): POST-action evaluation (OTAR Reflect)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from atlas.orchestration.types import Action, Observation


@dataclass(frozen=True)
class ReflectionResult:
    """Outcome of reflecting on an action's result."""
    succeeded: bool
    learnings: list[str] = field(default_factory=list)
    should_adjust_plan: bool = False
    adjustment_reason: str | None = None


class ReflectionHook(Protocol):
    async def critique(self, action: Action, context: str) -> Action:
        """Pre-action critique: revise or abort a risky action."""
        ...

    async def reflect(self, action: Action, observation: Observation, context: str) -> ReflectionResult:
        """Post-action evaluation: assess outcome and extract learnings."""
        ...


class NoOpReflection:
    """Default pass-through: no pre-critique, optimistic post-reflection."""

    async def critique(self, action: Action, context: str) -> Action:
        return action

    async def reflect(self, action: Action, observation: Observation, context: str) -> ReflectionResult:
        succeeded = observation.success if hasattr(observation, "success") else True
        return ReflectionResult(succeeded=succeeded)

