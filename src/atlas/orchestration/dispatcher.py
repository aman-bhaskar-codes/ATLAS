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
from atlas.orchestration.tool_routing import ToolHealthTracker
from atlas.orchestration.types import Action, Observation
from atlas.safety.engine import DeniedError, HaltedError, SafetyEngine


class ToolDispatcher:
    def __init__(
        self,
        registry: ToolRegistry,
        safety: SafetyEngine,
        health: ToolHealthTracker | None = None,
    ) -> None:
        self._registry = registry
        self._safety = safety
        self._health = health

    async def dispatch(self, action: Action, correlation_id: CorrelationId) -> Observation:
        if action.tool is None or action.operation is None:
            return Observation(step=action.step, ok=False, error="action missing tool/operation")
        tool = self._registry.get(action.tool)
        if tool is None:
            return Observation(step=action.step, ok=False, error=f"unknown tool {action.tool!r}")
        import time as _time

        _started = _time.perf_counter()
        # Merge the action's operation into args so that tool.execute() can read it.
        # The Safety Engine uses action.operation for tier lookup; tools read args["operation"].
        merged_args = {"operation": action.operation, **action.args}
        req = ToolRequest(
            correlation_id=correlation_id,
            tool=action.tool,
            operation=action.operation,
            args=merged_args,
        )
        try:
            result = await self._safety.guard(req, tool)
        except HaltedError as exc:
            return Observation(step=action.step, ok=False, error=f"halted: {exc}")
        except DeniedError as exc:
            # a denial is information the model should see, not a crash
            return Observation(
                step=action.step,
                ok=False,
                error=f"denied (tier {exc.decision.tier.name}): {exc.decision.reason}",
            )
        except Exception as exc:
            raise ToolExecutionError(f"{action.tool}.{action.operation} failed: {exc}") from exc
        if self._health is not None:
            self._health.record(
                action.tool,
                ok=result.ok,
                latency_ms=int((_time.perf_counter() - _started) * 1000),
            )
        return Observation(step=action.step, ok=result.ok, content=result.output, error=result.error)
