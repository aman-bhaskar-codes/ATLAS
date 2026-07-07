"""Tool dispatcher — action -> Safety Engine -> observation.

WHY it goes through guard(): the orchestrator must NEVER execute a tool directly.
Every dispatch is classified, policy-checked, audited, sandboxed, and (for Tier-2)
confirmed by L1. A denial or halt becomes a structured Observation the loop can
reason about, not a crash.
"""

from __future__ import annotations

from atlas.infra.ids import CorrelationId
from atlas.infra.types import ToolRequest
from atlas.orchestration.errors import ToolExecutionError
from atlas.orchestration.registry import ToolRegistry
from atlas.orchestration.types import Action, Observation
from atlas.safety.engine import DeniedError, HaltedError, SafetyEngine


class ToolDispatcher:
    def __init__(self, registry: ToolRegistry, safety: SafetyEngine) -> None:
        self._registry = registry
        self._safety = safety

    async def dispatch(self, action: Action, correlation_id: CorrelationId) -> Observation:
        if action.tool is None or action.operation is None:
            return Observation(step=action.step, ok=False, error="action missing tool/operation")
        tool = self._registry.get(action.tool)
        if tool is None:
            return Observation(step=action.step, ok=False,
                               error=f"unknown tool {action.tool!r}")
        req = ToolRequest(
            correlation_id=correlation_id, tool=action.tool,
            operation=action.operation, args=action.args,
        )
        try:
            result = await self._safety.guard(req, tool)
        except HaltedError as exc:
            return Observation(step=action.step, ok=False, error=f"halted: {exc}")
        except DeniedError as exc:
            # a denial is information the model should see, not a crash
            return Observation(
                    step=action.step, ok=False,
                    error=f"denied (tier {exc.decision.tier.name}): "
                          f"{exc.decision.reason}",
                )
        except Exception as exc:
            raise ToolExecutionError(f"{action.tool}.{action.operation} failed: {exc}") from exc
        return Observation(step=action.step, ok=result.ok,
                           content=result.output, error=result.error)
