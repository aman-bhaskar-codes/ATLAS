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
from atlas.orchestration.errors import ReasoningError
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

    async def execute(
        self,
        plan: Plan,
        correlation_id: CorrelationId,
    ) -> dict[int, Observation]:
        """Run executable steps in dependency batches, concurrently where legal.

        Returns step_index → Observation for every attempted step. A failed
        step blocks its dependents (they are skipped, not attempted) — the
        reasoning loop decides how to recover.

        A shared LimitCounter is created per execution — same shape as the
        serial loop's per-task counter. Steps, tool calls, tokens, and
        runtime are all bounded. When a budget trips, the batch's remaining
        siblings are cancelled (asyncio.gather is used with explicit tasks)
        and the run terminates with an honest failure result.
        """
        counter = LimitCounter(self._limits)
        executable = {s.index: s for s in self.executable_steps(plan)}
        results: dict[int, Observation] = {}
        succeeded: set[int] = set()
        remaining = dict(executable)

        while remaining:
            batch = [s for s in remaining.values() if all(d in succeeded for d in s.depends_on if d in executable)]
            if not batch:
                _log.warning(
                    "dag.no_progress",
                    event_type="orchestration",
                    correlation_id=correlation_id,
                    blocked=sorted(remaining),
                )
                break

            tasks = [
                asyncio.ensure_future(self._run_step(step, correlation_id, counter))
                for step in batch
            ]
            try:
                outcomes = await asyncio.gather(*tasks)
            except ReasoningError:
                # Budget tripped — cancel any still-running siblings,
                # then record the failure for each step in this batch.
                for t in tasks:
                    if not t.done():
                        t.cancel()
                # Await cancellation to avoid orphaned coroutines.
                await asyncio.gather(*tasks, return_exceptions=True)
                for step in batch:
                    if step.index not in results:
                        results[step.index] = Observation(
                            step=step.index,
                            ok=False,
                            error=(
                                f"token budget exceeded "
                                f"({counter.tokens}/{self._limits.max_tokens} tokens consumed)"
                            ),
                        )
                    del remaining[step.index]
                # No more batches — budget is exhausted.
                break

            for step, obs in zip(batch, outcomes, strict=True):
                results[step.index] = obs
                if obs.ok:
                    succeeded.add(step.index)
                else:
                    _log.info(
                        "dag.step_failed",
                        event_type="orchestration",
                        correlation_id=correlation_id,
                        step=step.index,
                        error=(obs.error or "")[:120],
                    )
                del remaining[step.index]

        return results

    async def _run_step(
        self,
        step: PlanStep,
        correlation_id: CorrelationId,
        counter: LimitCounter,
    ) -> Observation:
        async with self._sem:
            action = Action(
                step=step.index,
                kind="tool_call",
                tool=step.tool,
                operation=step.operation,
                args=dict(step.args),
            )
            obs = await self._dispatcher.dispatch(action, correlation_id)

            # Tick limits — same synchronous-tick pattern the serial loop
            # uses. add_tokens(0) for unmeasured usage is deliberate: the
            # step/tool/runtime limits still bound the run.
            counter.tick_step()
            counter.tick_tool()
            counter.add_tokens(obs.usage.total if obs.usage else 0)

            return obs
