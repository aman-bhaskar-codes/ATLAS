"""ToolingExecutor — the universal execution facade (Part 1 §27).

Its job is NOT to replace existing dispatch logic; it resolves definition +
adapter from the registry and lets the adapter reach the existing governed
funnels:

    UniversalToolInvocation
        -> registry.resolve (definition + adapter)
        -> state checks (disabled tools never execute, §74)
        -> adapter.execute -> ToolDispatcher | CapabilityDispatcher
        -> SafetyEngine    (inside the dispatchers)
        -> UniversalToolResult

Every execution emits structured observability (tool id, operation, adapter,
task id, correlation id, duration) through the existing structlog + Metrics
infrastructure — no second telemetry system (§41).
"""

from __future__ import annotations

import time

from atlas.infra.logging import get_logger
from atlas.infra.metrics import Metrics
from atlas.tooling.errors import ToolAdapterError, ToolDisabled, ToolNotFound
from atlas.tooling.models.tool_health import ToolRuntimeState
from atlas.tooling.models.tool_invocation import UniversalToolInvocation
from atlas.tooling.models.tool_result import UniversalToolResult
from atlas.tooling.registry.registry import ToolingRegistry

_log = get_logger("atlas.tooling.executor")


class ToolingExecutor:
    def __init__(self, *, registry: ToolingRegistry, metrics: Metrics | None = None) -> None:
        self._registry = registry
        self._metrics = metrics

    def _observe(self, event: str, **fields: object) -> None:
        _log.info(event, event_type="tooling", **fields)

    async def execute(self, invocation: UniversalToolInvocation) -> UniversalToolResult:
        registration = self._registry.get(invocation.tool_id)
        if registration is None:
            raise ToolNotFound(f"tool {invocation.tool_id!r} is not registered in the tooling registry")
        definition = registration.definition
        state = registration.status.state
        if state == ToolRuntimeState.DISABLED:
            raise ToolDisabled(f"tool {invocation.tool_id!r} is disabled and cannot execute")
        if state == ToolRuntimeState.FAILED:
            raise ToolAdapterError(
                f"tool {invocation.tool_id!r} failed initialization and cannot execute "
                f"({registration.status.detail or 'no detail'})"
            )
        if state == ToolRuntimeState.REGISTERED:
            raise ToolAdapterError(
                f"tool {invocation.tool_id!r} was never initialized; call ToolingFabric.initialize() first"
            )

        started = time.perf_counter()
        self._observe(
            "tooling.execution.started",
            tool_id=invocation.tool_id,
            operation=invocation.operation,
            adapter=registration.adapter.kind,
            task_id=invocation.task_id,
            correlation_id=str(invocation.correlation_id),
        )
        result = await registration.adapter.execute(invocation, definition)
        duration_ms = int((time.perf_counter() - started) * 1000)
        if result.duration_ms is None:
            result = result.model_copy(update={"duration_ms": duration_ms})

        if result.ok:
            self._observe(
                "tooling.execution.completed",
                tool_id=invocation.tool_id,
                operation=invocation.operation,
                adapter=registration.adapter.kind,
                task_id=invocation.task_id,
                correlation_id=str(invocation.correlation_id),
                duration_ms=duration_ms,
            )
        else:
            self._observe(
                "tooling.execution.failed",
                tool_id=invocation.tool_id,
                operation=invocation.operation,
                adapter=registration.adapter.kind,
                task_id=invocation.task_id,
                correlation_id=str(invocation.correlation_id),
                duration_ms=duration_ms,
                failure_kind=result.error.kind.value if result.error else "unknown",
            )
        if self._metrics is not None:
            self._metrics.observe("tooling.execution.duration_ms", float(duration_ms))
            self._metrics.counter("tooling.executions", 1)
        return result
