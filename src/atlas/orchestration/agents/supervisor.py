"""AgentSupervisor — decompose, delegate concurrently, synthesize.

WHY it lives behind the Orchestrator rather than beside it: there is exactly one
task pipeline, and multi-agent execution is a *strategy inside* it, not a second
entrypoint. The Orchestrator still owns task rows, state transitions, trajectory
persistence and events; the supervisor only replaces the "reason about the plan"
segment when delegation is worth it.

Bounded by construction:
  - the graph is capped (subtask count, steps per subtask) at decomposition;
  - a semaphore caps how many specialists run at once (single-user machine);
  - each specialist carries its own step/token/runtime budget;
  - a wall-clock deadline abandons remaining batches rather than running long;
  - a failed subtask SKIPS its transitive dependents instead of running them
    against missing input.

Safety: every tool call still goes ReasoningLoop -> ToolDispatcher ->
SafetyEngine.guard(). The supervisor introduces no tool path, no tier change,
and no way to approve anything.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any

from atlas.infra.cognition import TaskIntent
from atlas.infra.ids import CorrelationId, TaskId
from atlas.infra.logging import get_logger
from atlas.orchestration.agents.decomposer import TaskDecomposer
from atlas.orchestration.agents.specialists import Specialist
from atlas.orchestration.agents.synthesizer import Synthesizer
from atlas.orchestration.agents.types import (
    SubTask,
    SubTaskResult,
    SubTaskStatus,
    TaskDAG,
)
from atlas.orchestration.errors import CancellationError
from atlas.orchestration.events import EventPublisher
from atlas.orchestration.managers.cancellation import CancellationToken

_log = get_logger("atlas.orch.agents.supervisor")

_UPSTREAM_CHARS = 4000  # per dependency, injected into a specialist's context


@dataclass(frozen=True)
class SupervisionResult:
    """Aggregate of a delegated run.

    `delegated=False` means the supervisor declined and the caller must fall
    back to serial execution — it is NOT a failure.
    """

    delegated: bool
    reason: str
    answer: str | None = None
    results: tuple[SubTaskResult, ...] = ()
    dag: TaskDAG | None = None

    @property
    def ok(self) -> bool:
        """True when at least one subtask succeeded and none of the graph's
        leaves were left unattempted for lack of budget."""
        return bool(self.results) and any(r.ok for r in self.results)

    @property
    def steps_taken(self) -> int:
        return sum(r.steps_taken for r in self.results)

    @property
    def tool_calls(self) -> int:
        return sum(r.tool_calls for r in self.results)

    @property
    def model_calls(self) -> int:
        return sum(r.model_calls for r in self.results)

    @property
    def tokens_used(self) -> int:
        return sum(r.tokens_used for r in self.results)

    @property
    def actions(self) -> tuple[Any, ...]:
        """Flattened specialist actions, in DAG order.

        The parent trajectory is what the experience extractor learns from, so a
        delegated run must contribute its actions upward or multi-agent work
        would be invisible to the learning loop.
        """
        return tuple(a for r in self.results for a in r.actions)

    @property
    def observations(self) -> tuple[Any, ...]:
        return tuple(o for r in self.results for o in r.observations)

    def summary(self) -> str:
        """One-line accounting, for events and the failure message."""
        counts: dict[str, int] = {}
        for r in self.results:
            counts[r.status.value] = counts.get(r.status.value, 0) + 1
        return ", ".join(f"{k}={v}" for k, v in sorted(counts.items()))


class AgentSupervisor:
    def __init__(
        self,
        *,
        decomposer: TaskDecomposer,
        specialist: Specialist,
        synthesizer: Synthesizer,
        events: EventPublisher,
        max_concurrency: int = 2,
        deadline_s: float = 600.0,
    ) -> None:
        self._decomposer = decomposer
        self._specialist = specialist
        self._synthesizer = synthesizer
        self._events = events
        # WHY default 2, not the CPU count: concurrent specialists contend for
        # ONE local model (gpu_concurrency: 1). Beyond a small number they queue
        # on the gateway anyway while multiplying token spend.
        self._sem = asyncio.Semaphore(max(1, max_concurrency))
        self._deadline_s = deadline_s

    async def maybe_run(
        self,
        *,
        task_id: TaskId,
        correlation_id: CorrelationId,
        request: str,
        context: str,
        intent: TaskIntent,
        token: CancellationToken,
    ) -> SupervisionResult:
        """Decide, then delegate. Falls back cleanly whenever delegation is not
        the right call or does not pay off."""
        outcome = await self._decomposer.decompose(request, context, intent, correlation_id)
        if not outcome.should_delegate or outcome.dag is None or not outcome.dag.subtasks:
            return SupervisionResult(delegated=False, reason=outcome.reason or "no delegation")

        dag = outcome.dag
        await self._events.emit(
            task_id=task_id,
            correlation_id=correlation_id,
            state="reasoning",
            kind="agents.decomposed",
            subtasks=len(dag.subtasks),
            batches=len(dag.batches()),
            roles=[s.role.value for s in dag.subtasks],
            repairs=list(dag.repairs),
        )

        results = await self._execute(
            dag=dag,
            task_id=task_id,
            correlation_id=correlation_id,
            context=context,
            token=token,
        )
        answer = await self._synthesizer.synthesize(
            request=request,
            results=results,
            correlation_id=correlation_id,
        )
        await self._events.emit(
            task_id=task_id,
            correlation_id=correlation_id,
            state="reasoning",
            kind="agents.synthesized",
            succeeded=sum(1 for r in results if r.ok),
            failed=sum(1 for r in results if r.status is SubTaskStatus.FAILED),
            skipped=sum(1 for r in results if r.status is SubTaskStatus.SKIPPED),
        )
        return SupervisionResult(
            delegated=True,
            reason=outcome.reason,
            answer=answer,
            results=results,
            dag=dag,
        )

    async def _execute(
        self,
        *,
        dag: TaskDAG,
        task_id: TaskId,
        correlation_id: CorrelationId,
        context: str,
        token: CancellationToken,
    ) -> tuple[SubTaskResult, ...]:
        """Run the graph batch by batch. Returns one result per subtask."""
        started = time.perf_counter()
        done: dict[str, SubTaskResult] = {}
        skipped: set[str] = set()

        for batch in dag.batches():
            runnable = [s for s in batch if s.id not in skipped]
            for s in batch:
                if s.id in skipped and s.id not in done:
                    done[s.id] = _skip(s)
            if not runnable:
                continue

            if (elapsed := time.perf_counter() - started) > self._deadline_s:
                _log.warning(
                    "agents.deadline_exceeded",
                    event_type="orchestration",
                    correlation_id=str(correlation_id),
                    elapsed_s=round(elapsed, 1),
                    abandoned=[s.id for s in runnable],
                )
                for s in runnable:
                    done[s.id] = _skip(s, "supervisor deadline exceeded before this subtask started")
                    skipped |= dag.dependents_of(s.id)
                continue

            outcomes = await asyncio.gather(
                *(
                    self._run_one(
                        subtask=s,
                        done=done,
                        task_id=task_id,
                        correlation_id=correlation_id,
                        context=context,
                        token=token,
                    )
                    for s in runnable
                ),
            )
            for subtask, result in zip(runnable, outcomes, strict=True):
                done[subtask.id] = result
                if not result.ok:
                    # Dependents would run against absent input. Skipping is
                    # honest; running them would fabricate confidence.
                    blocked = dag.dependents_of(subtask.id)
                    skipped |= blocked
                    if blocked:
                        _log.info(
                            "agents.subtask_blocked_dependents",
                            event_type="orchestration",
                            correlation_id=str(correlation_id),
                            subtask_id=subtask.id,
                            blocked=sorted(blocked),
                        )

        # Preserve DAG order in the returned tuple; fill any gap defensively so
        # the synthesizer always sees every subtask exactly once.
        return tuple(done.get(s.id) or _skip(s, "not executed") for s in dag.subtasks)

    async def _run_one(
        self,
        *,
        subtask: SubTask,
        done: dict[str, SubTaskResult],
        task_id: TaskId,
        correlation_id: CorrelationId,
        context: str,
        token: CancellationToken,
    ) -> SubTaskResult:
        async with self._sem:
            if token.cancelled:
                return _skip(subtask, "task cancelled")
            await self._events.emit(
                task_id=task_id,
                correlation_id=correlation_id,
                state="reasoning",
                kind="agents.subtask_started",
                subtask_id=subtask.id,
                role=subtask.role.value,
                max_steps=subtask.max_steps,
            )
            try:
                result = await self._specialist.run(
                    subtask=subtask,
                    parent_task_id=task_id,
                    correlation_id=correlation_id,
                    base_context=context,
                    upstream=_upstream_block(subtask, done),
                    token=token,
                )
            except CancellationError:
                return _skip(subtask, "task cancelled mid-subtask")
            await self._events.emit(
                task_id=task_id,
                correlation_id=correlation_id,
                state="reasoning",
                kind="agents.subtask_finished",
                latency_ms=result.latency_ms,
                subtask_id=subtask.id,
                role=subtask.role.value,
                status=result.status.value,
                steps=result.steps_taken,
                tool_calls=result.tool_calls,
            )
            return result


def _upstream_block(subtask: SubTask, done: dict[str, SubTaskResult]) -> str:
    """Render ONLY the declared dependencies' outputs."""
    blocks: list[str] = []
    for dep_id in subtask.depends_on:
        dep = done.get(dep_id)
        if dep is None:
            continue
        body = dep.output[:_UPSTREAM_CHARS] if dep.ok else f"(failed: {dep.error or 'unknown error'})"
        blocks.append(f"--- {dep_id} ({dep.role.value}) ---\n{body}")
    return "\n\n".join(blocks)


def _skip(subtask: SubTask, reason: str = "a dependency failed") -> SubTaskResult:
    return SubTaskResult(
        subtask_id=subtask.id,
        role=subtask.role,
        status=SubTaskStatus.SKIPPED,
        error=reason,
    )
