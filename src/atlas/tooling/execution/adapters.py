"""Step adapters — the ONE governed execution boundary per candidate type
(Part 4 §13-§17/§85-§88).

Every adapter converges on governed execution: TOOL steps run through the
Part-1 `ToolingExecutor` (→ ToolDispatcher/CapabilityDispatcher →
SafetyEngine); WORKFLOW steps re-enter the engine with bounded depth (§17-§18);
AGENT steps delegate to an injected runtime handler (the router owns
selection, the agent runtime owns its reasoning, §16). No ungoverned
side paths exist.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from atlas.tooling.execution.models import ExecutionObservation, RetryClass
from atlas.tooling.execution.retry import classify_exception, classify_result
from atlas.tooling.models.tool_invocation import InvocationSource, UniversalToolInvocation
from atlas.tooling.models.tool_result import UniversalToolResult


class CandidateNotExecutable(Exception):  # noqa: N818 — reads as a noun in except clauses
    """A step's candidate cannot run through any registered adapter."""


@dataclass(frozen=True)
class StepContext:
    """Everything a step execution needs, resolved by the engine."""

    run_id: str
    task_id: str
    correlation_id: str
    step_id: str
    attempt: int
    candidate_id: str
    operation: str | None
    arguments: dict[str, Any]
    timeout_s: float


class StepAdapter(Protocol):
    candidate_type: str  # "tool" | "agent" | "workflow"

    async def execute(self, context: StepContext) -> UniversalToolResult: ...


class ToolStepAdapter:
    """§13/§14/§15: tools execute through the Part-1 governed executor —
    ToolDispatcher/CapabilityDispatcher → SafetyEngine. Nothing is duplicated."""

    candidate_type = "tool"

    def __init__(self, tooling_executor: Any) -> None:
        self._executor = tooling_executor

    async def execute(self, context: StepContext) -> UniversalToolResult:
        invocation = UniversalToolInvocation(
            tool_id=context.candidate_id,
            operation=context.operation or "read",
            arguments=dict(context.arguments),
            correlation_id=context.correlation_id,  # type: ignore[arg-type]
            task_id=context.task_id,
            source=InvocationSource.SYSTEM,
            timeout_s=context.timeout_s,
            idempotency_key=f"{context.task_id}:{context.run_id}:{context.step_id}:{context.attempt}",
        )
        result: UniversalToolResult = await self._executor.execute(invocation)
        return result


class AgentStepAdapter:
    """§16: a route step whose candidate is an agent delegates to an injected
    handler (the future Research/IDE runtimes). The adapter preserves the SAME
    semantics — timeout, retry, checkpoint, observation — around the handler."""

    candidate_type = "agent"

    def __init__(self, handler: Any) -> None:
        """``handler``: async (StepContext) -> UniversalToolResult."""
        self._handler = handler

    async def execute(self, context: StepContext) -> UniversalToolResult:
        if self._handler is None:
            raise CandidateNotExecutable("no agent runtime is registered for agent steps")
        result: UniversalToolResult = await self._handler(context)
        return result


def observation_from_result(step_id: str, result: UniversalToolResult, duration_ms: int) -> ExecutionObservation:
    """§54: normalize any governed result into an execution observation."""
    error_class = RetryClass.UNKNOWN
    error_message = None
    if not result.ok:
        error_class = classify_result(result)
        error_message = result.error.message if result.error else "execution failed"
    return ExecutionObservation(
        step_id=step_id,
        ok=result.ok,
        summary=_summarize(result),
        data=result.data if _is_small(result.data) else None,
        data_ref=None,  # artifact references arrive with the artifact store (§53)
        error=error_message,
        error_class=error_class,
        provider=result.provider,
        duration_ms=duration_ms,
        side_effects=tuple(f"{se.kind}:{se.target}" for se in result.side_effects),
        metadata=dict(result.metadata),
    )


def observation_from_exception(step_id: str, exc: BaseException, duration_ms: int) -> ExecutionObservation:
    return ExecutionObservation(
        step_id=step_id,
        ok=False,
        summary=f"exception: {type(exc).__name__}",
        error=f"{type(exc).__name__}: {exc}",
        error_class=classify_exception(exc),
        duration_ms=duration_ms,
    )


def _summarize(result: UniversalToolResult) -> str:
    if result.ok:
        data = result.data
        text = data if isinstance(data, str) else str(data)
        return text[:300]
    return (result.error.message if result.error else "failed")[:300]


def _is_small(data: Any) -> bool:
    """§53/§55: only bounded values travel inline; large outputs stay behind
    references. (Artifact persistence lands with the artifact store.)"""
    if isinstance(data, str):
        return len(data) <= 4000
    if isinstance(data, (dict, list)):
        return len(str(data)) <= 4000
    return True


__all__ = [
    "AgentStepAdapter",
    "CandidateNotExecutable",
    "StepAdapter",
    "StepContext",
    "ToolStepAdapter",
    "observation_from_exception",
    "observation_from_result",
]


class WorkflowStepAdapter:
    """§17/§18: a route step whose candidate is a reusable workflow re-enters
    the engine with the workflow's RoutePlan — with a HARD depth bound so
    recursive route explosion is impossible."""

    candidate_type = "workflow"

    def __init__(self, engine_factory: Any, *, max_depth: int = 2) -> None:
        """``engine_factory(depth) -> ExecutionEngine`` — the bootstrap supplies
        child engines at decreasing depth."""
        self._engine_factory = engine_factory
        self._max_depth = max_depth
        self._plans: dict[str, Any] = {}

    def register_workflow(self, workflow_id: str, plan: Any) -> None:
        self._plans[workflow_id] = plan

    async def execute(self, context: StepContext) -> UniversalToolResult:
        plan = self._plans.get(context.candidate_id)
        if plan is None:
            raise CandidateNotExecutable(f"workflow {context.candidate_id!r} is not registered")
        if self._max_depth <= 0:
            raise CandidateNotExecutable("max nested workflow depth exceeded (§18)")
        from atlas.tooling.execution.models import ExecutionRequest
        from atlas.tooling.models.tool_result import UniversalToolResult

        child = self._engine_factory(depth=self._max_depth - 1)
        result = await child.start(
            ExecutionRequest(
                plan=plan,
                task_id=context.task_id,
                correlation_id=context.correlation_id,
            )
        )
        ok = result.outcome.value in ("SUCCESS", "PARTIAL_SUCCESS")
        return UniversalToolResult(
            ok=ok,
            tool_id=context.candidate_id,
            data={"run_id": result.run_id, "outcome": result.outcome.value, "outputs": result.outputs},
            error=None if ok else result.summary,
        )
