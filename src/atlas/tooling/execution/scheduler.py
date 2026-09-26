"""Dependency-aware step scheduling + join semantics (Part 4 §9-§10/§41-§42/§60-§61).

Dependencies come EXPLICITLY from the plan's ``depends_on`` — never inferred
from ordering (§9). A step is ready only when its dependency policy is
satisfied (§10): all deps succeeded, or the step's declared join policy
tolerates partial failure (§41 — e.g. "2 of 4 research sources"). Dead ends
(§61) are detected structurally instead of hanging.
"""

from __future__ import annotations

from dataclasses import dataclass

from atlas.tooling.execution.models import JoinPolicy, StepRun, StepStatus
from atlas.tooling.routing.models import RoutePlan, RouteStep

_TERMINAL_OK = frozenset({StepStatus.SUCCEEDED, StepStatus.SKIPPED})
_TERMINAL_BAD = frozenset({StepStatus.FAILED, StepStatus.CANCELLED, StepStatus.BLOCKED})
_UNFINISHED = frozenset(
    {
        StepStatus.PENDING,
        StepStatus.READY,
        StepStatus.RUNNING,
        StepStatus.RETRYING,
        StepStatus.FALLBACK,
        StepStatus.WAITING,
    }
)


def _join_of(step: RouteStep) -> tuple[JoinPolicy, int]:
    """Read the step's declared join policy (§41). Steps declare it via
    metadata: {"join": {"policy": "MIN_SUCCESS_COUNT", "min_success": 2}}.
    Default: ALL_REQUIRED."""
    join = step.input_mapping.get("join") or {}
    policy = JoinPolicy(join.get("policy", JoinPolicy.ALL_REQUIRED.value))
    min_success = int(join.get("min_success", 0))
    return policy, min_success


def _deps_satisfied(step: RouteStep, states: dict[str, StepRun]) -> tuple[bool, str]:
    deps = [d for d in step.depends_on if d in states]
    if not deps:
        return True, "no dependencies"
    ok = sum(1 for d in deps if states[d].status in _TERMINAL_OK)
    failed = sum(1 for d in deps if states[d].status in _TERMINAL_BAD)
    pending = len(deps) - ok - failed
    if pending > 0:
        return False, f"{pending} dependency(ies) still running"

    policy, min_success = _join_of(step)
    if policy == JoinPolicy.ALL_REQUIRED:
        return (failed == 0), f"ALL_REQUIRED: {ok} ok / {failed} failed"
    if policy == JoinPolicy.ALL_BEST_EFFORT:
        return (ok > 0 or failed < len(deps)), f"ALL_BEST_EFFORT: {ok} ok / {failed} failed"
    if policy == JoinPolicy.MIN_SUCCESS_COUNT:
        return (ok >= min_success), f"MIN_SUCCESS_COUNT {ok}/{min_success}"
    if policy == JoinPolicy.MIN_SUCCESS_RATIO:
        ratio = ok / len(deps) if deps else 0.0
        return (ratio >= (min_success / 100.0 if min_success > 1 else 0.5)), (f"MIN_SUCCESS_RATIO {ok}/{len(deps)}")
    if policy == JoinPolicy.FIRST_SUCCESS:
        return (ok >= 1), f"FIRST_SUCCESS: {ok} ok"
    return (failed == 0), "default ALL_REQUIRED"


@dataclass(frozen=True)
class ScheduleView:
    ready: tuple[RouteStep, ...]
    running: tuple[RouteStep, ...]
    dead_end: bool
    all_done: bool
    succeeded: int
    failed: int
    reason: str = ""


class StepScheduler:
    """Computes the runnable set from the plan + current step states (§10)."""

    def __init__(self, plan: RoutePlan) -> None:
        self._plan = plan
        self._by_id = {s.step_id: s for s in plan.steps}

    def view(self, states: dict[str, StepRun]) -> ScheduleView:
        ready: list[RouteStep] = []
        running: list[RouteStep] = []
        reasons: list[str] = []
        succeeded = failed = 0
        unfinished = 0

        for step in self._plan.steps:
            state = states.get(step.step_id)
            status = state.status if state else StepStatus.PENDING
            if status in _TERMINAL_OK:
                succeeded += 1
                continue
            if status in _TERMINAL_BAD:
                failed += 1
                continue
            if status == StepStatus.RUNNING:
                running.append(step)
                unfinished += 1
                continue
            # PENDING/READY/RETRYING/FALLBACK/WAITING: is it schedulable now?
            satisfied, reason = _deps_satisfied(step, states)
            unfinished += 1  # a ready/pending step is NOT done (§60: completion
            # is a policy question — every non-terminal step counts)
            if satisfied:
                ready.append(step)
            else:
                if "still running" in reason:
                    reasons.append(f"{step.step_id}: waiting on dependencies")
                else:
                    # dependency FAILED and the join policy does not tolerate it —
                    # this step is BLOCKED forever (§61 dead-end material).
                    states[step.step_id] = (
                        states.get(step.step_id) or StepRun(step_id=step.step_id, candidate_id=step.candidate_id)
                    ).model_copy(update={"status": StepStatus.BLOCKED})
                    reasons.append(f"{step.step_id}: blocked — {reason}")

        all_done = unfinished == 0
        # §61: nothing runnable, nothing running, but steps remain unfinished
        # (all blocked) → structural dead end.
        dead_end = (not ready) and (not running) and unfinished > 0
        return ScheduleView(
            ready=tuple(ready),
            running=tuple(running),
            dead_end=dead_end,
            all_done=all_done,
            succeeded=succeeded,
            failed=failed,
            reason="; ".join(reasons[:3]),
        )


def terminal_outcome_for_view(view: ScheduleView, *, cancelled: bool) -> str:
    """§59/§60/§93: completion is a POLICY question, not 'no runnable steps'."""
    if cancelled:
        return "CANCELLED"
    if view.all_done:
        if view.failed == 0:
            return "SUCCESS"
        # partial success is legitimate when the route tolerated failures
        if view.succeeded > 0:
            return "PARTIAL_SUCCESS"
        return "FAILED"
    if view.dead_end:
        return "DEAD_END"
    return "RUNNING"


__all__ = ["ScheduleView", "StepScheduler", "terminal_outcome_for_view"]
