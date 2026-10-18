"""DAG-aware execution of fully-specified plan steps.

WHY: the OTAR loop reasons one action at a time — right for exploration, wrong
for a plan whose steps are already concrete (tool + operation + args known).
This executor runs independent steps CONCURRENTLY in topological batches,
every dispatch still flowing through ToolDispatcher → SafetyEngine.guard().
Bounded: a semaphore caps concurrency (single-user machine) and only steps
whose dependency steps ALL succeeded are attempted; anything ambiguous stays
with the reasoning loop.

Safety invariants: identical path per step as the serial loop — no shortcut,
no tier bypass, no new policy.
"""

from __future__ import annotations

import asyncio

from atlas.infra.ids import CorrelationId
from atlas.infra.logging import get_logger
from atlas.orchestration.dispatcher import ToolDispatcher
from atlas.orchestration.errors import OrchestrationTimeoutError, ReasoningError
from atlas.orchestration.limits import ExecutionLimits, LimitCounter
from atlas.orchestration.types import Action, Observation, Plan, PlanStep

_log = get_logger("atlas.orch.dag")

_MAX_CONCURRENCY = 3


class DagExecutor:
    def __init__(
        self,
        dispatcher: ToolDispatcher,
        max_concurrency: int = _MAX_CONCURRENCY,
        limits: ExecutionLimits | None = None,
    ) -> None:
        self._dispatcher = dispatcher
        self._sem = asyncio.Semaphore(max_concurrency)
        self._limits = limits or ExecutionLimits()

    def executable_steps(self, plan: Plan) -> tuple[PlanStep, ...]:
        """Steps that are fully specified (tool + operation) and safe to batch."""
        return tuple(s for s in plan.steps if s.tool and s.operation and s.index not in s.depends_on)

    @staticmethod
    def _graph_error(plan: Plan) -> str | None:
        steps = {step.index: step for step in plan.steps}
        if len(steps) != len(plan.steps):
            return "invalid dependency graph: duplicate step indices"
        dependencies = {index: set(step.depends_on) for index, step in steps.items()}
        children: dict[int, list[int]] = {index: [] for index in steps}
        for index, deps in dependencies.items():
            if index in deps:
                return f"invalid dependency graph: step {index} depends on itself"
            missing = deps - steps.keys()
            if missing:
                return f"invalid dependency graph: step {index} depends on missing steps {sorted(missing)}"
            for dep in deps:
                children[dep].append(index)
        ready = [index for index, deps in dependencies.items() if not deps]
        visited = 0
        while ready:
            index = ready.pop()
            visited += 1
            for child in children[index]:
                dependencies[child].remove(index)
                if not dependencies[child]:
                    ready.append(child)
        if visited != len(steps):
            return "invalid dependency graph: cycle detected"
        return None

    async def execute(
        self,
        plan: Plan,
        correlation_id: CorrelationId,
    ) -> dict[int, Observation]:
        """Run executable steps in dependency batches, concurrently where legal.

        Returns step_index → Observation for attempted steps and explicit
        validation/limit failures for undispatched work. A failed tool step
        blocks its dependents (they are skipped, not attempted) — the reasoning
        loop decides how to recover.

        A shared LimitCounter is created per execution — same shape as the
        serial loop's per-task counter. Steps, tool calls, tokens, and
        runtime are checked before admission. When a budget trips, admitted
        calls finish and retain their real observations; undispatched steps
        receive the limiting reason. Token use is known only after completion,
        so an admitted concurrent batch may exceed its token allowance.
        """
        counter = LimitCounter(self._limits)
        graph_error = self._graph_error(plan)
        if graph_error:
            return {
                step.index: Observation(step=step.index, ok=False, error=graph_error)
                for step in plan.steps if step.tool and step.operation
            }
        executable = {s.index: s for s in self.executable_steps(plan)}
        results: dict[int, Observation] = {}
        succeeded: set[int] = set()
        remaining = dict(executable)

        while remaining:
            batch = [s for s in remaining.values() if all(d in succeeded for d in s.depends_on)]
            if not batch:
                _log.warning(
                    "dag.no_progress",
                    event_type="orchestration",
                    correlation_id=correlation_id,
                    blocked=sorted(remaining),
                )
                break

            tasks = [
                asyncio.create_task(self._run_step(step, correlation_id, counter, results))
                for step in batch
            ]
            outcomes = await asyncio.gather(*tasks, return_exceptions=True)
            limit_error: str | None = None
            for step, obs in zip(batch, outcomes, strict=True):
                if isinstance(obs, asyncio.CancelledError):
                    raise obs
                if isinstance(obs, BaseException):
                    if isinstance(obs, (ReasoningError, OrchestrationTimeoutError)):
                        limit_error = limit_error or str(obs)
                    # A post-dispatch accounting error cannot undo an observation.
                    results.setdefault(step.index, Observation(step=step.index, ok=False, error=str(obs)))
                else:
                    results[step.index] = obs
                recorded = results[step.index]
                if recorded.ok:
                    succeeded.add(step.index)
                else:
                    _log.info(
                        "dag.step_failed",
                        event_type="orchestration",
                        correlation_id=correlation_id,
                        step=step.index,
                        error=(recorded.error or "")[:120],
                    )
                del remaining[step.index]
            if limit_error is not None:
                _log.warning(
                    "dag.limited",
                    event_type="orchestration",
                    correlation_id=correlation_id,
                    error=limit_error,
                    tokens=counter.tokens,
                )
                for index in remaining:
                    results[index] = Observation(step=index, ok=False, error=limit_error)
                break

        return results

    async def _run_step(
        self,
        step: PlanStep,
        correlation_id: CorrelationId,
        counter: LimitCounter,
        results: dict[int, Observation],
    ) -> Observation:
        async with self._sem:
            counter.reserve_tool_step()
            action = Action(
                step=step.index,
                kind="tool_call",
                tool=step.tool,
                operation=step.operation,
                args=dict(step.args),
            )
            obs = await self._dispatcher.dispatch(action, correlation_id)
            results[step.index] = obs
            counter.add_tokens(obs.usage.total if obs.usage else 0)

            return obs
