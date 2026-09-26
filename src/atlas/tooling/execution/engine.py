"""ExecutionEngine — the durable route-plan runtime (Part 4 §1/§99-§101).

Takes a Part-3 RoutePlan and RELIABLY executes it:
    validate → schedule → slots → staleness revalidation → governed funnel
    (ToolingExecutor → SafetyEngine) → observation → validate → retry/fallback/
    replan/human/terminate — with SQLite checkpoints at every boundary and
    resume from the last one (§21/§68/§70).

Boundaries kept clean (§3/§95/§96): the router owns selection; the engine owns
HOW execution runs; the adapters translate; the SafetyEngine authorizes; the
Orchestrator remains the task lifecycle owner. No external orchestrator
dependency (§90) — Temporal-inspired semantics on ATLAS's own SQLite/bus
(§2; ADR in docs/execution/architecture.md).
"""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from atlas.infra.config import ExecutionCfg
from atlas.infra.logging import get_logger
from atlas.orchestration.managers.cancellation import CancellationToken
from atlas.tooling.execution.adapters import (
    AgentStepAdapter,
    CandidateNotExecutable,
    StepAdapter,
    StepContext,
    ToolStepAdapter,
    observation_from_exception,
    observation_from_result,
)
from atlas.tooling.execution.events import (
    KIND_RUN_COMPLETED,
    KIND_RUN_FAILED,
    KIND_RUN_READY,
    KIND_RUN_STARTED,
    KIND_STEP_COMPLETED,
    KIND_STEP_FAILED,
    KIND_STEP_RETRYING,
    KIND_STEP_STARTED,
    KIND_STEP_TIMED_OUT,
    ExecutionEventPublisher,
)
from atlas.tooling.execution.models import (
    ExecutionObservation,
    ExecutionRequest,
    ExecutionRun,
    ExecutionRunResult,
    RetryClass,
    RunStatus,
    StepRun,
    StepStatus,
    TerminalOutcome,
)
from atlas.tooling.execution.recovery import (
    RecoveryContext,
    RecoveryController,
    RouteStalenessChecker,
    recovery_outcome_for,
)
from atlas.tooling.execution.retry import classify_result
from atlas.tooling.execution.scheduler import ScheduleView, StepScheduler, terminal_outcome_for_view
from atlas.tooling.execution.slots import ExecutionSlots
from atlas.tooling.execution.store import ExecutionRunStore
from atlas.tooling.models.tool_result import UniversalToolResult
from atlas.tooling.routing.models import RecoveryAction, RoutePlan, RouteStep

_log = get_logger("atlas.tooling.execution.engine")

ReplanHook = Callable[[ExecutionRun, RouteStep, str], Awaitable[RoutePlan | None]]


class ExecutionValidationError(Exception):
    """§98: the plan is invalid — DO NOT execute."""


class ExecutionEngine:
    def __init__(
        self,
        *,
        config: ExecutionCfg,
        store: ExecutionRunStore,
        tooling_executor: Any,
        catalog: Any | None = None,
        cascade: Any | None = None,
        bus: Any | None = None,
        agent_handler: Any | None = None,
        replan_hook: ReplanHook | None = None,
        workflow_depth: int = 2,
    ) -> None:
        self._config = config
        self._store = store
        self._slots = ExecutionSlots(max_concurrent_steps=config.max_concurrent_steps)
        self._staleness = RouteStalenessChecker(catalog)
        self._recovery = RecoveryController(cascade=cascade, staleness=self._staleness, enable_judgment=bool(cascade))
        self._events = ExecutionEventPublisher(bus)
        self._adapters: dict[str, StepAdapter] = {"tool": ToolStepAdapter(tooling_executor)}
        if agent_handler is not None:
            self._adapters["agent"] = AgentStepAdapter(agent_handler)
        self._replan_hook = replan_hook
        self._workflow_depth = workflow_depth
        self._tokens: dict[str, CancellationToken] = {}
        self._pause_requests: set[str] = set()
        self._plans: dict[str, RoutePlan] = {}
        self._decisions: dict[str, Any] = {}

    # ── Public API (§99) ──────────────────────────────────────────── #

    async def start(self, request: ExecutionRequest) -> ExecutionRunResult:
        """Execute a RoutePlan to a terminal outcome."""
        run = self._create_run(request)
        run = run.transition(RunStatus.VALIDATING)
        await self._persist(run)

        plan = request.plan
        problems = self._validate_plan(plan)
        if problems:
            run = run.transition(RunStatus.FAILED)
            run = run.model_copy(update={"outcome": TerminalOutcome.BLOCKED, "error": "; ".join(problems)})
            await self._persist(run)
            await self._events.publish(
                KIND_RUN_FAILED,
                run_id=run.run_id,
                task_id=run.task_id,
                correlation_id=run.correlation_id,
                reason="plan validation failed",
            )
            return self._result(run, summary=f"plan validation failed: {'; '.join(problems)}")

        run = run.transition(RunStatus.READY)
        await self._events.publish(
            KIND_RUN_READY, run_id=run.run_id, task_id=run.task_id, correlation_id=run.correlation_id
        )
        return await self._drive(run, plan)

    async def pause(self, run_id: str) -> bool:
        """Cooperative pause at the next step boundary (§47)."""
        self._pause_requests.add(run_id)
        return True

    async def resume(self, run_id: str) -> ExecutionRunResult | None:
        """§21/§68: load checkpoint → validate schema/plan → restore → continue.
        Completed steps are NOT re-run (§70/§118)."""
        checkpoint = await self._store.load_latest_checkpoint(run_id)
        if checkpoint is None:
            return None
        run = ExecutionRun.model_validate(checkpoint["run"])
        plan_data = checkpoint.get("plan")
        if plan_data is None:
            raise ExecutionValidationError(f"checkpoint for {run_id} carries no plan; cannot resume")
        plan = RoutePlan.model_validate(plan_data)
        if run.status not in (RunStatus.PAUSED, RunStatus.WAITING_HUMAN):
            return None
        if run.run_id not in self._tokens:
            self._tokens[run.run_id] = CancellationToken()
        self._plans[run.run_id] = plan
        await self._events.publish(
            "execution.resumed", run_id=run.run_id, task_id=run.task_id, correlation_id=run.correlation_id
        )
        return await self._drive(run, plan)

    async def cancel(self, run_id: str) -> bool:
        """§45: cooperative cancellation — checked at every step boundary and
        inside the slot-acquired execution path."""
        token = self._tokens.get(run_id)
        if token is not None:
            token.cancel()
        return True

    async def status(self, run_id: str) -> ExecutionRun | None:
        return await self._store.load_run(run_id)

    async def list_runs(self, *, task_id: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
        return await self._store.list_runs(task_id=task_id, limit=limit)

    async def retry(self, run_id: str) -> ExecutionRunResult | None:
        """Re-arm a FAILED run (§99): failed/blocked steps go back to READY."""
        run = await self._store.load_run(run_id)
        if run is None or run.status != RunStatus.FAILED:
            return None
        plan = await self._load_plan(run)
        if plan is None:
            return None
        reset = tuple(
            s.model_copy(update={"status": StepStatus.READY})
            if s.status in (StepStatus.FAILED, StepStatus.BLOCKED)
            else s
            for s in run.steps
        )
        run = run.model_copy(update={"steps": reset, "outcome": None, "error": None}).transition(RunStatus.RUNNING)
        await self._persist(run)
        return await self._drive(run, plan)

    # ── Wiring helpers ─────────────────────────────────────────────── #

    def attach_decision(self, plan_id: str, decision: Any) -> None:
        """The router hands the decision (fallback chain) to the run (§29)."""
        self._decisions[plan_id] = decision

    # ── Run creation + validation (§6/§98) ────────────────────────── #

    def _create_run(self, request: ExecutionRequest) -> ExecutionRun:
        run_id = request.run_id or f"run-{uuid.uuid4().hex[:12]}"
        self._tokens[run_id] = CancellationToken()
        steps = tuple(StepRun(step_id=step.step_id, candidate_id=step.candidate_id) for step in request.plan.steps)
        run = ExecutionRun(
            run_id=run_id,
            task_id=request.task_id,
            correlation_id=request.correlation_id,
            route_plan_id=request.plan.plan_id,
            route_id=request.route_id,
            steps=steps,
            limits={
                "max_retries": float(self._config.retry_max_attempts),
                "max_fallback_hops": float(self._config.max_fallback_hops),
                "max_replans": float(self._config.max_replans),
                "step_timeout_s": self._config.default_step_timeout_s,
            },
            catalog_version=request.catalog_version,
            created_at=datetime.now(UTC),
        )
        self._plans[run_id] = request.plan
        return run

    def _validate_plan(self, plan: RoutePlan) -> list[str]:
        problems: list[str] = []
        if not plan.steps:
            problems.append("plan has no steps")
        step_ids = {s.step_id for s in plan.steps}
        if len(step_ids) != len(plan.steps):
            problems.append("duplicate step ids")
        for step in plan.steps:
            for dep in step.depends_on:
                if dep not in step_ids:
                    problems.append(f"step {step.step_id!r} depends on unknown {dep!r}")
            if not step.candidate_id and step.action == "execute":
                problems.append(f"step {step.step_id!r} has no candidate")
        if self._has_cycle(plan):
            problems.append("dependency cycle detected")  # §62
        return problems

    @staticmethod
    def _has_cycle(plan: RoutePlan) -> bool:
        graph: dict[str, list[str]] = {s.step_id: list(s.depends_on) for s in plan.steps}
        visiting: set[str] = set()
        done: set[str] = set()

        def visit(node: str) -> bool:
            if node in done:
                return False
            if node in visiting:
                return True
            visiting.add(node)
            if any(visit(dep) for dep in graph.get(node, ())):
                return True
            visiting.remove(node)
            done.add(node)
            return False

        return any(visit(node) for node in graph)

    async def _load_plan(self, run: ExecutionRun) -> RoutePlan | None:
        stored = self._plans.get(run.run_id)
        if stored is not None:
            return stored
        checkpoint = await self._store.load_latest_checkpoint(run.run_id)
        if checkpoint and checkpoint.get("plan"):
            return RoutePlan.model_validate(checkpoint["plan"])
        return None

    # ── The drive loop (§101) ─────────────────────────────────────── #

    async def _drive(self, run: ExecutionRun, plan: RoutePlan) -> ExecutionRunResult:
        started = time.perf_counter()
        scheduler = StepScheduler(plan)
        run = run.transition(RunStatus.RUNNING)
        await self._events.publish(
            KIND_RUN_STARTED, run_id=run.run_id, task_id=run.task_id, correlation_id=run.correlation_id
        )
        await self._persist(run)
        await self._checkpoint(run, plan, boundary="run_started")

        transitions = 0
        max_transitions = 200  # §62: graph-transition guard
        while transitions < max_transitions:
            transitions += 1
            token = self._tokens.get(run.run_id)
            if token is not None and token.cancelled:
                return await self._finish(
                    run, plan, scheduler.view({s.step_id: s for s in run.steps}), "CANCELLED", started
                )

            states = {s.step_id: s for s in run.steps}
            view = scheduler.view(states)
            outcome_name = terminal_outcome_for_view(view, cancelled=False)
            if outcome_name != "RUNNING":
                return await self._finish(run, plan, view, outcome_name, started)

            if not view.ready:
                return await self._finish(run, plan, view, "DEAD_END", started)

            # §47: pause at the boundary — checkpoint, resumable.
            if run.run_id in self._pause_requests:
                self._pause_requests.discard(run.run_id)
                run = run.transition(RunStatus.PAUSED)
                await self._checkpoint(run, plan, boundary="paused")
                return self._result(run, started=started, summary="paused at step boundary")

            # §11/§12: bounded parallel dispatch of independent ready steps.
            batch = view.ready[: self._config.max_parallel_group]
            step_runs = await asyncio.gather(*(self._execute_step_with_recovery(run, step, plan) for step in batch))
            for step_run in step_runs:
                run = self._merge_step(run, step_run)
            await self._persist(run)

            # A step entered WAITING (human) → the run waits (§47/§93).
            if any(s.status == StepStatus.WAITING for s in run.steps):
                run = run.transition(RunStatus.WAITING_HUMAN)
                run = run.model_copy(update={"outcome": TerminalOutcome.WAITING_HUMAN})
                await self._persist(run)
                await self._checkpoint(run, plan, boundary="waiting_human")
                return self._result(run, started=started, summary="waiting for human decision")

        run = run.transition(RunStatus.FAILED)
        run = run.model_copy(update={"outcome": TerminalOutcome.DEAD_END, "error": "max graph transitions exceeded"})
        await self._persist(run)
        return self._result(run, started=started)

    async def _execute_step_with_recovery(self, run: ExecutionRun, step: RouteStep, plan: RoutePlan) -> StepRun:
        """One step through the full boundary, including its retry/fallback
        ladder. Returns the final StepRun snapshot (§80: no shared mutable
        step state)."""
        state = run.step(step.step_id) or StepRun(step_id=step.step_id, candidate_id=step.candidate_id)
        token = self._tokens.get(run.run_id)

        if state.status == StepStatus.PENDING:
            state = state.transition(StepStatus.READY)  # §8: READY is mandatory

        # §30-§31: revalidate the candidate against the CURRENT catalog before
        # executing — never trust a stale route blindly.
        if self._staleness is not None and step.candidate_id:
            valid, reason = self._staleness.revalidate(
                state.candidate_id or step.candidate_id,
                route_catalog_version=run.catalog_version,
            )
            if not valid:
                _log.warning(
                    "execution.step.stale_candidate",
                    event_type="execution",
                    run_id=run.run_id,
                    step_id=step.step_id,
                    reason=reason,
                )
                observation = ExecutionObservation(
                    step_id=step.step_id,
                    ok=False,
                    summary="candidate failed revalidation",
                    error=reason,
                    error_class=RetryClass.NOT_FOUND,  # not retryable → recovery routes to fallback
                )
                state = state.transition(StepStatus.RUNNING)
                return await self._handle_failure(run, step, plan, state, observation)

        state = state.transition(StepStatus.RUNNING)
        state = state.model_copy(update={"attempt": state.attempt + 1, "started_at": datetime.now(UTC)})
        await self._events.publish(
            KIND_STEP_STARTED,
            run_id=run.run_id,
            task_id=run.task_id,
            step_id=step.step_id,
            correlation_id=run.correlation_id,
            attempt=state.attempt,
            candidate_id=step.candidate_id,
        )

        timeout_s = step.timeout_s or self._config.default_step_timeout_s
        context = StepContext(
            run_id=run.run_id,
            task_id=run.task_id,
            correlation_id=run.correlation_id,
            step_id=step.step_id,
            attempt=state.attempt,
            candidate_id=state.candidate_id or step.candidate_id or "",
            operation=step.operation,
            arguments=dict(step.input_mapping.get("arguments", {})),
            timeout_s=timeout_s,
        )
        adapter = self._adapter_for(step)
        started = time.perf_counter()
        exec_task: asyncio.Task[UniversalToolResult] | None = None
        watcher: asyncio.Task[None] | None = None
        try:
            if token is not None and token.cancelled:
                return state.transition(StepStatus.CANCELLED)
            async with self._slots.acquire(context.candidate_id):
                if token is not None and token.cancelled:  # cancelled while waiting (§75)
                    return state.transition(StepStatus.CANCELLED)
                exec_task = asyncio.ensure_future(adapter.execute(context))
                if token is not None:
                    watcher = asyncio.ensure_future(self._watch_token(token, exec_task))
                result = await asyncio.wait_for(asyncio.shield(exec_task), timeout=timeout_s)
        except TimeoutError:
            if exec_task is not None and not exec_task.done():
                exec_task.cancel()  # §43/§46: the timed-out work is stopped, not orphaned
            observation = ExecutionObservation(
                step_id=step.step_id,
                ok=False,
                summary="step timed out",
                error=f"timed out after {timeout_s}s",
                error_class=RetryClass.TIMEOUT,
                duration_ms=int((time.perf_counter() - started) * 1000),
            )
            await self._events.publish(
                KIND_STEP_TIMED_OUT,
                run_id=run.run_id,
                task_id=run.task_id,
                step_id=step.step_id,
                correlation_id=run.correlation_id,
            )
            return await self._handle_failure(run, step, plan, state, observation)
        except CandidateNotExecutable as exc:
            observation = observation_from_exception(step.step_id, exc, int((time.perf_counter() - started) * 1000))
            return await self._handle_failure(run, step, plan, state, observation)
        except asyncio.CancelledError:
            return state.transition(StepStatus.CANCELLED)
        except Exception as exc:
            observation = observation_from_exception(step.step_id, exc, int((time.perf_counter() - started) * 1000))
            return await self._handle_failure(run, step, plan, state, observation)
        finally:
            if watcher is not None:
                watcher.cancel()

        duration = int((time.perf_counter() - started) * 1000)
        observation = observation_from_result(step.step_id, result, duration)
        if observation.ok:
            state = state.transition(StepStatus.SUCCEEDED)
            state = state.model_copy(
                update={"observations": (*state.observations, observation), "completed_at": datetime.now(UTC)}
            )
            await self._events.publish(
                KIND_STEP_COMPLETED,
                run_id=run.run_id,
                task_id=run.task_id,
                step_id=step.step_id,
                correlation_id=run.correlation_id,
                duration_ms=duration,
                summary=observation.summary[:120],
            )
            return state
        return await self._handle_failure(run, step, plan, state, observation, result=result)

    async def _handle_failure(
        self,
        run: ExecutionRun,
        step: RouteStep,
        plan: RoutePlan,
        state: StepRun,
        observation: ExecutionObservation,
        *,
        result: Any | None = None,
    ) -> StepRun:
        """§44/§63: classify → recovery controller → bounded action. The
        controller decides; the engine executes the decision (§63)."""
        failure_class = observation.error_class
        state = state.model_copy(
            update={
                "observations": (*state.observations, observation),
                "last_failure_class": failure_class,
                "last_failure": observation.error,
            }
        )
        await self._events.publish(
            KIND_STEP_FAILED,
            run_id=run.run_id,
            task_id=run.task_id,
            step_id=step.step_id,
            correlation_id=run.correlation_id,
            error_class=failure_class.value,
            error=observation.error,
        )

        decision = self._decisions.get(plan.plan_id)
        fallbacks: tuple[str, ...] = tuple(decision.fallback_candidates) if decision else ()
        ctx = RecoveryContext(
            route_id=run.route_id or run.run_id,
            step_id=step.step_id,
            candidate_id=step.candidate_id or "",
            failure_class=failure_class,
            failure_message=observation.error or "",
            attempt=state.attempt,
            retry_policy_max_attempts=self._config.retry_max_attempts,
            fallback_candidates=fallbacks,
            fallback_cursor=state.fallback_cursor,
            fallback_count=run.fallback_count,
            max_fallback_hops=self._config.max_fallback_hops,
            replan_count=run.replan_count,
            max_replans=self._config.max_replans,
            idempotent=not self._candidate_is_side_effecting(step.candidate_id),
            side_effects=self._candidate_is_side_effecting(step.candidate_id),
        )
        decision = await self._recovery.decide(ctx)

        if decision.action == RecoveryAction.RETRY:
            state = state.transition(StepStatus.RETRYING)
            await self._events.publish(
                KIND_STEP_RETRYING,
                run_id=run.run_id,
                task_id=run.task_id,
                step_id=step.step_id,
                correlation_id=run.correlation_id,
                attempt=state.attempt + 1,
                delay_s=decision.delay_s,
            )
            if decision.delay_s > 0:
                await asyncio.sleep(decision.delay_s)
            # READY re-enters scheduling with the same candidate (§62-bounded
            # by the controller's attempt check).
            return state.model_copy(update={"status": StepStatus.READY})

        if decision.action == RecoveryAction.FALLBACK:
            cursor = state.fallback_cursor
            next_candidate = decision.next_candidate_id
            if next_candidate is None:
                cursor += 1
                next_candidate = fallbacks[cursor] if cursor < len(fallbacks) else step.candidate_id
            await self._events.publish(
                "fallback.selected",
                run_id=run.run_id,
                task_id=run.task_id,
                step_id=step.step_id,
                correlation_id=run.correlation_id,
                candidate_id=next_candidate,
            )
            return state.model_copy(
                update={
                    "status": StepStatus.READY,
                    "candidate_id": next_candidate,
                    "fallback_cursor": cursor + 1,
                }
            )

        if decision.action == RecoveryAction.REPLAN:
            run = run.model_copy(update={"replan_count": run.replan_count + 1})
            new_plan = None
            if self._replan_hook is not None:
                new_plan = await self._replan_hook(run, step, observation.error or "step failed")
            await self._events.publish(
                "execution.replanned",
                run_id=run.run_id,
                task_id=run.task_id,
                correlation_id=run.correlation_id,
                step_id=step.step_id,
                replanned=new_plan is not None,
            )
            if new_plan is not None:
                self._plans[run.run_id] = new_plan
                return state.model_copy(update={"status": StepStatus.READY})
            return state.transition(StepStatus.FAILED)

        # HUMAN / TERMINATE / SKIP
        outcome = decision.terminal_outcome or recovery_outcome_for(decision.action)
        if outcome == TerminalOutcome.WAITING_HUMAN:
            return state.transition(StepStatus.WAITING)
        return state.transition(StepStatus.FAILED)

    @staticmethod
    async def _watch_token(token: CancellationToken, exec_task: asyncio.Task[UniversalToolResult]) -> None:
        """§45/§46: propagate cancellation INTO the running step cooperatively —
        the adapter's awaited work is cancelled, not merely abandoned."""
        while not token.cancelled:  # noqa: ASYNC110 — plain-flag token poll, no Event to await
            await asyncio.sleep(0.01)
        if not exec_task.done():
            exec_task.cancel()

    def _candidate_is_side_effecting(self, candidate_id: str | None) -> bool:
        """§24/§85: honor Part-1 tool metadata for retry safety — metadata is
        never stronger than policy, but sufficient to refuse blind retries."""
        catalog = getattr(self._staleness, "_catalog", None)
        if catalog is None or candidate_id is None:
            return False
        record = catalog.inspect(candidate_id)
        return bool(record and record.side_effects)

    def _adapter_for(self, step: RouteStep) -> StepAdapter:
        candidate_type = step.candidate_type.value
        adapter = self._adapters.get(candidate_type)
        if adapter is None:
            raise CandidateNotExecutable(f"no adapter registered for candidate type {candidate_type!r}")
        return adapter

    def _merge_step(self, run: ExecutionRun, step_run: StepRun) -> ExecutionRun:
        steps = tuple(step_run if s.step_id == step_run.step_id else s for s in run.steps)
        attempt_count = sum(s.attempt for s in steps)
        fallback_count = max(run.fallback_count, step_run.fallback_cursor)
        return run.model_copy(update={"steps": steps, "attempt_count": attempt_count, "fallback_count": fallback_count})

    async def _finish(
        self,
        run: ExecutionRun,
        plan: RoutePlan,
        view: ScheduleView,
        outcome_name: str,
        started: float,
    ) -> ExecutionRunResult:
        outcome = TerminalOutcome(outcome_name)
        if outcome in (TerminalOutcome.SUCCESS, TerminalOutcome.PARTIAL_SUCCESS):
            run = run.transition(RunStatus.COMPLETED)
        elif outcome == TerminalOutcome.CANCELLED:
            run = run.transition(RunStatus.CANCELLED)
        else:
            run = run.transition(RunStatus.FAILED)
        run = run.model_copy(update={"outcome": outcome, "completed_at": datetime.now(UTC)})
        await self._persist(run)
        await self._checkpoint(run, plan, boundary="run_finished")
        kind = (
            KIND_RUN_COMPLETED
            if outcome in (TerminalOutcome.SUCCESS, TerminalOutcome.PARTIAL_SUCCESS)
            else KIND_RUN_FAILED
        )
        await self._events.publish(
            kind,
            run_id=run.run_id,
            task_id=run.task_id,
            correlation_id=run.correlation_id,
            outcome=outcome.value,
            succeeded=view.succeeded,
            failed=view.failed,
        )
        return self._result(run, started=started)

    # ── Persistence + results ─────────────────────────────────────── #

    async def _persist(self, run: ExecutionRun) -> None:
        try:
            await self._store.save_run(run)
        except Exception as exc:
            _log.error("execution.persist.failed", event_type="execution", run_id=run.run_id, error=repr(exc))

    async def _checkpoint(
        self, run: ExecutionRun, plan: RoutePlan, *, boundary: str, step_id: str | None = None
    ) -> None:
        if not self._config.checkpoint_enabled:
            return
        try:
            await self._store.save_checkpoint(run, boundary=boundary, step_id=step_id, plan=plan)
        except Exception as exc:
            _log.error("execution.checkpoint.failed", event_type="execution", run_id=run.run_id, error=repr(exc))

    def _result(self, run: ExecutionRun, *, started: float | None = None, summary: str = "") -> ExecutionRunResult:
        completed = tuple(s.step_id for s in run.steps if s.status == StepStatus.SUCCEEDED)
        failed = tuple(s.step_id for s in run.steps if s.status == StepStatus.FAILED)
        outputs = {
            s.step_id: s.observations[-1].data
            for s in run.steps
            if s.observations and s.observations[-1].data is not None
        }
        duration = int((time.perf_counter() - started) * 1000) if started is not None else 0
        return ExecutionRunResult(
            run_id=run.run_id,
            task_id=run.task_id,
            status=run.status,
            outcome=run.outcome or TerminalOutcome.FAILED,
            summary=summary or (run.error or ""),
            outputs=outputs,
            completed_steps=completed,
            failed_steps=failed,
            attempts=run.attempt_count,
            fallbacks=run.fallback_count,
            replans=run.replan_count,
            duration_ms=duration,
        )

    @staticmethod
    def _classify(result: Any) -> RetryClass:
        return classify_result(result) if isinstance(result, UniversalToolResult) else RetryClass.UNKNOWN


__all__ = ["ExecutionEngine", "ExecutionValidationError"]
