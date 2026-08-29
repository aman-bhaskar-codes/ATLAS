"""AgentSupervisor — decompose, delegate concurrently, synthesize, verify.

WHY it lives behind the Orchestrator rather than beside it: there is exactly one
task pipeline, and multi-agent execution is a *strategy inside* it, not a second
entrypoint. The Orchestrator still owns task rows, state transitions, trajectory
persistence and events; the supervisor only replaces the "reason about the plan"
segment when delegation is worth it.

Bounded by construction:
  - the graph is capped (subtask count, steps per subtask) at decomposition;
  - a semaphore caps how many specialists run at once (single-user machine);
  - each specialist carries its own step/token/runtime budget;
  - a run-wide token budget abandons remaining batches once spend is exhausted;
  - a wall-clock deadline abandons remaining batches rather than running long;
  - a failed subtask SKIPS its transitive dependents instead of running them
    against missing input.

Verified, not assumed: a specialist reporting SUCCEEDED means its own loop
finished, which is not evidence that the request was answered. After synthesis
the supervisor runs the SAME Verifier the serial path uses (deterministic
checks first — see orchestration/verification.py) plus a deterministic
cross-branch conflict scan, and derives a RunOutcome from that. There is no
verifier agent persona: an LLM asked to bless another LLM's answer is a second
opinion, not verification.

Safety: every tool call still goes ReasoningLoop -> ToolDispatcher ->
SafetyEngine.guard(). The supervisor introduces no tool path, no tier change,
and no way to approve anything. Verification runs through the same funnel.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any

from atlas.infra.cognition import Evidence, TaskIntent, VerificationResult
from atlas.infra.ids import CorrelationId, TaskId
from atlas.infra.logging import get_logger
from atlas.orchestration.agents.adjudication import ClaimConflict, detect_conflicts
from atlas.orchestration.agents.decomposer import TaskDecomposer
from atlas.orchestration.agents.specialists import Specialist
from atlas.orchestration.agents.synthesizer import Synthesizer
from atlas.orchestration.agents.types import (
    RunOutcome,
    SubTask,
    SubTaskResult,
    SubTaskStatus,
    TaskDAG,
)
from atlas.orchestration.errors import CancellationError
from atlas.orchestration.events import EventPublisher
from atlas.orchestration.goal import GoalState, Verifier
from atlas.orchestration.managers.cancellation import CancellationToken

_log = get_logger("atlas.orch.agents.supervisor")

_UPSTREAM_CHARS = 4000  # per dependency, injected into a specialist's context
_EVIDENCE_CHARS = 240  # per subtask, in the evidence handed to the verifier


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
    # Independent verification of the synthesized answer. None means no verifier
    # was wired, which is reported as UNCERTAIN — never as verified.
    verification: VerificationResult | None = None
    conflicts: tuple[ClaimConflict, ...] = field(default_factory=tuple)
    # Subtasks never attempted because the run ran out of time or tokens, as
    # opposed to being skipped because a dependency failed. An incomplete graph
    # cannot produce a verified answer.
    abandoned: tuple[str, ...] = field(default_factory=tuple)

    @property
    def ok(self) -> bool:
        """True when the run produced something worth returning."""
        return bool(self.results) and any(r.ok for r in self.results)

    @property
    def outcome(self) -> RunOutcome:
        """Derived, never stored — so no caller can assert a verdict it did not earn."""
        if not self.ok or not (self.answer or "").strip():
            return RunOutcome.FAILED
        v = self.verification
        if v is None:
            return RunOutcome.UNCERTAIN  # nothing checked it
        if not v.passed:
            # A crashed verifier means "not verified", not "proven wrong".
            reason = v.failure_reason or ""
            return RunOutcome.UNCERTAIN if reason.startswith("verifier_error") else RunOutcome.REJECTED
        if v.verifier == "none":
            # not_applicable(): passed=True but nothing was actually checked.
            return RunOutcome.UNCERTAIN
        if self.conflicts or self.abandoned:
            return RunOutcome.UNCERTAIN
        return RunOutcome.VERIFIED

    @property
    def verified(self) -> bool:
        return self.outcome is RunOutcome.VERIFIED

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
        verifier: Verifier | None = None,
        max_concurrency: int = 2,
        deadline_s: float = 600.0,
        max_total_tokens: int = 0,
    ) -> None:
        self._decomposer = decomposer
        self._specialist = specialist
        self._synthesizer = synthesizer
        self._events = events
        # The SAME verifier instance the serial path uses. Passing it in rather
        # than constructing one here is what keeps there being a single
        # verification system (deterministic checks first, model opinion last).
        self._verifier = verifier
        # WHY default 2, not the CPU count: concurrent specialists contend for
        # ONE local model (gpu_concurrency: 1). Beyond a small number they queue
        # on the gateway anyway while multiplying token spend.
        self._sem = asyncio.Semaphore(max(1, max_concurrency))
        self._deadline_s = deadline_s
        # Run-wide ceiling. Per-subtask token limits bound one specialist; they
        # do not bound a graph, so a wide DAG could multiply spend by its width.
        # 0 disables the check.
        self._max_total_tokens = max(0, max_total_tokens)

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

        execution = await self._execute(
            dag=dag,
            task_id=task_id,
            correlation_id=correlation_id,
            context=context,
            token=token,
        )
        results = execution.results
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
            abandoned=list(execution.abandoned),
        )

        # Independent checks, in order of trustworthiness: a deterministic scan
        # for contradictions between branches, then the shared Verifier.
        conflicts = detect_conflicts(results)
        verification = await self._verify(
            request=request,
            intent=intent,
            answer=answer,
            results=results,
            context=context,
            correlation_id=correlation_id,
        )

        supervision = SupervisionResult(
            delegated=True,
            reason=outcome.reason,
            answer=answer,
            results=results,
            dag=dag,
            verification=verification,
            conflicts=conflicts,
            abandoned=execution.abandoned,
        )
        await self._events.emit(
            task_id=task_id,
            correlation_id=correlation_id,
            state="validating",
            kind="agents.verified",
            outcome=supervision.outcome.value,
            verifier=verification.verifier if verification else "none",
            verification_passed=bool(verification.passed) if verification else None,
            verification_score=round(verification.score, 3) if verification else None,
            conflicts=[c.describe() for c in conflicts],
        )
        return supervision

    async def _verify(
        self,
        *,
        request: str,
        intent: TaskIntent,
        answer: str,
        results: tuple[SubTaskResult, ...],
        context: str,
        correlation_id: CorrelationId,
    ) -> VerificationResult | None:
        """Check the synthesized answer against the ORIGINAL request's criteria.

        Fails CLOSED in the honest direction: a verifier that raises yields
        ``VerificationResult.error()`` (passed=False), which the outcome maps to
        UNCERTAIN — not to a pass, and not to a claim that the answer is wrong.
        Returns None only when no verifier is wired at all, which is likewise
        reported as UNCERTAIN.
        """
        if self._verifier is None:
            return None
        goal = GoalState(
            objective=intent.objective or request,
            success_criteria=intent.success_criteria,
            current_state="delegated",
        )
        try:
            return await self._verifier.verify(
                goal,
                answer,
                correlation_id,
                context,
                intent.domain,
                _evidence_from(results),
            )
        except Exception as exc:
            _log.warning(
                "agents.verification_failed",
                event_type="orchestration",
                correlation_id=str(correlation_id),
                error=repr(exc),
            )
            return VerificationResult.error(repr(exc), verifier=getattr(self._verifier, "name", "unknown"))

    async def _execute(
        self,
        *,
        dag: TaskDAG,
        task_id: TaskId,
        correlation_id: CorrelationId,
        context: str,
        token: CancellationToken,
    ) -> _Execution:
        """Run the graph batch by batch. Returns one result per subtask."""
        started = time.perf_counter()
        done: dict[str, SubTaskResult] = {}
        skipped: set[str] = set()
        abandoned: list[str] = []

        for batch in dag.batches():
            runnable = [s for s in batch if s.id not in skipped]
            for s in batch:
                if s.id in skipped and s.id not in done:
                    done[s.id] = _skip(s)
            if not runnable:
                continue

            if reason := self._exhausted(started, done):
                _log.warning(
                    "agents.budget_exhausted",
                    event_type="orchestration",
                    correlation_id=str(correlation_id),
                    reason=reason,
                    abandoned=[s.id for s in runnable],
                )
                for s in runnable:
                    done[s.id] = _skip(s, f"{reason} before this subtask started")
                    abandoned.append(s.id)
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
        results = tuple(done.get(s.id) or _skip(s, "not executed") for s in dag.subtasks)
        return _Execution(results=results, abandoned=tuple(abandoned))

    def _exhausted(self, started: float, done: dict[str, SubTaskResult]) -> str:
        """Return a reason string when no further batch may start."""
        if (elapsed := time.perf_counter() - started) > self._deadline_s:
            return f"supervisor deadline exceeded ({elapsed:.0f}s > {self._deadline_s:.0f}s)"
        if self._max_total_tokens:
            spent = sum(r.tokens_used for r in done.values())
            if spent >= self._max_total_tokens:
                return f"run token budget exhausted ({spent} >= {self._max_total_tokens})"
        return ""

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


@dataclass(frozen=True)
class _Execution:
    """Graph execution outcome: one result per subtask, plus what was abandoned.

    Abandonment is tracked separately from skipping because the two mean
    different things: a skipped subtask was correctly not attempted (its input
    never existed), whereas an abandoned one *should* have run and did not. Only
    the second makes the run's answer incomplete.
    """

    results: tuple[SubTaskResult, ...]
    abandoned: tuple[str, ...] = ()


def _evidence_from(results: tuple[SubTaskResult, ...]) -> tuple[Evidence, ...]:
    """Turn specialist outcomes into verifier evidence.

    Deterministic and derived from what actually ran — a subtask's id, role and
    self-reported status, never its claims. GroundingVerifier uses this to reject
    an answer that no branch actually supports.
    """
    return tuple(
        Evidence(
            source=r.subtask_id,
            operation=r.role.value,
            ok=r.ok,
            summary=(r.output[:_EVIDENCE_CHARS] if r.ok else (r.error or r.status.value)),
        )
        for r in results
    )


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
