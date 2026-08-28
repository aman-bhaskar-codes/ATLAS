"""Specialists — role-shaped execution of one SubTask.

WHY this is thin: a "specialist" is NOT a second runtime. It is the same
ReasoningLoop, given (a) a role-specific framing, (b) its own step/token budget,
and (c) only the upstream outputs it declared a dependency on. That is the whole
difference. Reusing the loop means specialists inherit critique, reflection,
verification, checkpointing, trajectory capture — and, critically, the identical
SafetyEngine funnel. There is no second path to a tool.

WHY each subtask gets a fresh TaskStateMachine: the machine is a per-execution
object with a legal-transition table. Sharing one across concurrent specialists
would make transitions race. The parent task's machine is advanced by the
Orchestrator, not by subtasks.
"""

from __future__ import annotations

import time

from atlas.infra.cognition import Complexity, TaskDomain, TaskIntent
from atlas.infra.ids import CorrelationId, TaskId
from atlas.infra.logging import get_logger
from atlas.orchestration.agents.types import AgentRole, SubTask, SubTaskResult, SubTaskStatus
from atlas.orchestration.errors import CancellationError
from atlas.orchestration.goal import GoalState
from atlas.orchestration.limits import ExecutionLimits
from atlas.orchestration.managers.cancellation import CancellationToken
from atlas.orchestration.reasoning import ReasoningLoop
from atlas.orchestration.state import TaskStateMachine
from atlas.orchestration.types import Capabilities, Plan, PlanStep

_log = get_logger("atlas.orch.agents.specialist")

# Role framing. Kept short on purpose: it is prepended to an already-large
# context, and long personas measurably crowd out retrieved evidence.
_ROLE_BRIEF: dict[AgentRole, str] = {
    AgentRole.RESEARCHER: (
        "You are the RESEARCHER. Gather evidence and cite where each claim came from. "
        "Never assert anything a tool result does not support. Say so when evidence is missing."
    ),
    AgentRole.CODER: (
        "You are the CODER. Read the real code before changing it. Make the smallest correct "
        "change, then verify it. Never guess at file contents or APIs."
    ),
    AgentRole.WRITER: (
        "You are the WRITER. Compose from the material supplied to you. Do not introduce facts "
        "that are not in that material."
    ),
    AgentRole.ANALYST: (
        "You are the ANALYST. Compare, quantify, and reason over the supplied data. "
        "State your assumptions and show the arithmetic behind any number."
    ),
    AgentRole.GENERAL: "You are a careful generalist. Prefer verifiable, reversible actions.",
}

_ROLE_DOMAIN: dict[AgentRole, TaskDomain] = {
    AgentRole.RESEARCHER: TaskDomain.RESEARCH,
    AgentRole.CODER: TaskDomain.CODING,
    AgentRole.WRITER: TaskDomain.CONVERSATION,
    AgentRole.ANALYST: TaskDomain.RESEARCH,
    AgentRole.GENERAL: TaskDomain.UNKNOWN,
}


class Specialist:
    """Executes one SubTask on the shared reasoning loop."""

    def __init__(
        self,
        reasoning: ReasoningLoop,
        *,
        max_tokens_per_subtask: int = 20_000,
        max_runtime_s: float = 180.0,
    ) -> None:
        self._reasoning = reasoning
        self._max_tokens = max_tokens_per_subtask
        self._max_runtime_s = max_runtime_s

    async def run(
        self,
        *,
        subtask: SubTask,
        parent_task_id: TaskId,
        correlation_id: CorrelationId,
        base_context: str,
        upstream: str,
        token: CancellationToken,
    ) -> SubTaskResult:
        """Run `subtask` and return its outcome.

        Never raises except for CancellationError, which must propagate so the
        supervisor can abandon the remaining batches promptly. Everything else
        becomes a FAILED result — one specialist failing is data for the
        synthesizer, not a reason to sink the parent task.
        """
        started = time.perf_counter()
        try:
            result = await self._reasoning.run(
                task_id=parent_task_id,
                correlation_id=correlation_id,
                plan=self._plan_for(subtask),
                context=self._context_for(subtask, base_context, upstream),
                # Fresh machine per subtask: transitions must not race.
                machine=TaskStateMachine(),
                token=token,
                goal=GoalState(
                    objective=subtask.objective,
                    success_criteria=subtask.success_criteria,
                    current_state="delegated",
                    # A subtask does not get its own replan budget on top of the
                    # parent's; the supervisor is the recovery layer here.
                    max_replans=1,
                ),
                caps=Capabilities(
                    needs_tools=bool(subtask.suggested_tools),
                    needs_reasoning=True,
                    max_risk=subtask.risk,
                ),
                intent=self._intent_for(subtask),
                limits=ExecutionLimits(
                    max_steps=subtask.max_steps,
                    max_tool_calls=subtask.max_steps * 2,
                    max_tokens=self._max_tokens,
                    max_runtime_s=self._max_runtime_s,
                ),
            )
        except CancellationError:
            raise
        except Exception as exc:
            _log.warning(
                "specialist.failed",
                event_type="orchestration",
                correlation_id=str(correlation_id),
                subtask_id=subtask.id,
                role=subtask.role.value,
                error=repr(exc),
            )
            return SubTaskResult(
                subtask_id=subtask.id,
                role=subtask.role,
                status=SubTaskStatus.FAILED,
                error=repr(exc),
                latency_ms=int((time.perf_counter() - started) * 1000),
            )

        return SubTaskResult(
            subtask_id=subtask.id,
            role=subtask.role,
            status=SubTaskStatus.SUCCEEDED if result.ok else SubTaskStatus.FAILED,
            output=(result.answer or "").strip(),
            error=result.error,
            steps_taken=result.steps_taken,
            tool_calls=result.tool_calls,
            model_calls=result.model_calls,
            tokens_used=result.tokens_used,
            latency_ms=result.latency_ms or int((time.perf_counter() - started) * 1000),
            actions=result.actions,
            observations=result.observations,
        )

    def _plan_for(self, subtask: SubTask) -> Plan:
        """A one-step exploratory plan.

        WHY not a fully-specified step: the supervisor decided WHAT to delegate,
        not which tool call achieves it. Leaving tool/operation unset is what
        keeps the subtask on the model-driven OTAR path (where critique and
        reflection apply) rather than the fully-specified DAG fast path.
        """
        hint = f" Suggested tools: {', '.join(subtask.suggested_tools)}." if subtask.suggested_tools else ""
        return Plan(
            goal=subtask.objective,
            constraints=(
                f"Stay strictly within this subtask: {subtask.objective}",
                "Do not attempt work assigned to other subtasks.",
                f"You have at most {subtask.max_steps} steps.",
            ),
            steps=(PlanStep(index=0, intent=subtask.objective + hint),),
            termination_conditions=subtask.success_criteria or ("the subtask objective is met",),
            risk=subtask.risk,
            confidence=0.6,
        )

    def _context_for(self, subtask: SubTask, base_context: str, upstream: str) -> str:
        criteria = "\n".join(f"- {c}" for c in subtask.success_criteria)
        parts = [
            _ROLE_BRIEF.get(subtask.role, _ROLE_BRIEF[AgentRole.GENERAL]),
            f"\nYOUR SUBTASK ({subtask.id}):\n{subtask.objective}",
        ]
        if criteria:
            parts.append(f"\nDONE WHEN:\n{criteria}")
        if upstream:
            # Only declared dependencies land here — a specialist must not be
            # able to read siblings it did not depend on, or the graph's meaning
            # (and its concurrency guarantee) would be fiction.
            parts.append(f"\nRESULTS FROM SUBTASKS YOU DEPEND ON:\n{upstream}")
        if base_context:
            parts.append(f"\nSHARED CONTEXT:\n{base_context}")
        return "\n".join(parts)

    def _intent_for(self, subtask: SubTask) -> TaskIntent:
        """Role-derived intent so the domain verifier router still applies."""
        return TaskIntent(
            objective=subtask.objective,
            domain=_ROLE_DOMAIN.get(subtask.role, TaskDomain.UNKNOWN),
            success_criteria=subtask.success_criteria,
            risk=subtask.risk,
            complexity=Complexity.SIMPLE,  # a subtask is simple by construction
            required_capabilities=subtask.suggested_tools,
            confidence=0.6,
        )
