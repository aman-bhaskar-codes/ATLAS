"""Execution fabric models (Part 4 §5-§8/§25-§26/§41/§93-§94).

Run and step state machines have EXPLICIT legal-transition tables (§7/§8): an
illegal transition raises immediately instead of mutating scattered booleans.
Terminal outcomes are structured (§93) — partial success, dead ends, budget
exhaustion and human waits are first-class, never collapsed into "failed".
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from atlas.tooling.routing.models import RoutePlan


class IllegalExecutionTransition(Exception):  # noqa: N818 — reads as a noun in except clauses
    """§7/§8: an illegal state transition — always a bug, never tolerated."""


class RunStatus(StrEnum):
    CREATED = "CREATED"
    VALIDATING = "VALIDATING"
    READY = "READY"
    RUNNING = "RUNNING"
    WAITING_DEPENDENCY = "WAITING_DEPENDENCY"
    WAITING_CONFIRMATION = "WAITING_CONFIRMATION"
    WAITING_HUMAN = "WAITING_HUMAN"
    RETRYING = "RETRYING"
    FALLING_BACK = "FALLING_BACK"
    REPLANNING = "REPLANNING"
    PAUSED = "PAUSED"
    CANCELLING = "CANCELLING"
    CANCELLED = "CANCELLED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


LEGAL_RUN_TRANSITIONS: dict[RunStatus, frozenset[RunStatus]] = {
    RunStatus.CREATED: frozenset({RunStatus.VALIDATING, RunStatus.CANCELLED}),
    RunStatus.VALIDATING: frozenset({RunStatus.READY, RunStatus.FAILED, RunStatus.CANCELLED}),
    RunStatus.READY: frozenset({RunStatus.RUNNING, RunStatus.CANCELLED, RunStatus.PAUSED}),
    RunStatus.RUNNING: frozenset(
        {
            RunStatus.WAITING_DEPENDENCY,
            RunStatus.WAITING_CONFIRMATION,
            RunStatus.WAITING_HUMAN,
            RunStatus.RETRYING,
            RunStatus.FALLING_BACK,
            RunStatus.REPLANNING,
            RunStatus.PAUSED,
            RunStatus.CANCELLING,
            RunStatus.COMPLETED,
            RunStatus.FAILED,
            RunStatus.CANCELLED,
        }
    ),
    RunStatus.WAITING_DEPENDENCY: frozenset({RunStatus.RUNNING, RunStatus.CANCELLING, RunStatus.FAILED}),
    RunStatus.WAITING_CONFIRMATION: frozenset({RunStatus.RUNNING, RunStatus.CANCELLING, RunStatus.FAILED}),
    RunStatus.WAITING_HUMAN: frozenset({RunStatus.RUNNING, RunStatus.CANCELLING, RunStatus.FAILED}),
    RunStatus.RETRYING: frozenset({RunStatus.RUNNING, RunStatus.FAILED, RunStatus.CANCELLED}),
    RunStatus.FALLING_BACK: frozenset({RunStatus.RUNNING, RunStatus.FAILED, RunStatus.CANCELLED}),
    RunStatus.REPLANNING: frozenset({RunStatus.RUNNING, RunStatus.FAILED, RunStatus.CANCELLED}),
    RunStatus.PAUSED: frozenset({RunStatus.RUNNING, RunStatus.CANCELLING, RunStatus.CANCELLED}),
    RunStatus.CANCELLING: frozenset({RunStatus.CANCELLED, RunStatus.FAILED}),
    # Terminal states transition nowhere.
    RunStatus.CANCELLED: frozenset(),
    RunStatus.COMPLETED: frozenset(),
    RunStatus.FAILED: frozenset({RunStatus.READY}),  # explicit retry(run_id) re-arms a failed run
}


def transition_run(current: RunStatus, new: RunStatus) -> RunStatus:
    if new not in LEGAL_RUN_TRANSITIONS[current]:
        raise IllegalExecutionTransition(f"illegal run transition {current.value} -> {new.value}")
    return new


class StepStatus(StrEnum):
    PENDING = "PENDING"
    READY = "READY"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    RETRYING = "RETRYING"
    FALLBACK = "FALLBACK"
    WAITING = "WAITING"
    SKIPPED = "SKIPPED"
    CANCELLED = "CANCELLED"
    BLOCKED = "BLOCKED"


LEGAL_STEP_TRANSITIONS: dict[StepStatus, frozenset[StepStatus]] = {
    StepStatus.PENDING: frozenset({StepStatus.READY, StepStatus.BLOCKED, StepStatus.SKIPPED, StepStatus.CANCELLED}),
    StepStatus.READY: frozenset({StepStatus.RUNNING, StepStatus.BLOCKED, StepStatus.SKIPPED, StepStatus.CANCELLED}),
    StepStatus.RUNNING: frozenset(
        {
            StepStatus.SUCCEEDED,
            StepStatus.FAILED,
            StepStatus.RETRYING,
            StepStatus.FALLBACK,
            StepStatus.WAITING,
            StepStatus.CANCELLED,
        }
    ),
    StepStatus.SUCCEEDED: frozenset(),
    StepStatus.FAILED: frozenset({StepStatus.RETRYING, StepStatus.FALLBACK, StepStatus.SKIPPED, StepStatus.BLOCKED}),
    StepStatus.RETRYING: frozenset({StepStatus.RUNNING, StepStatus.FAILED, StepStatus.FALLBACK, StepStatus.CANCELLED}),
    StepStatus.FALLBACK: frozenset({StepStatus.RUNNING, StepStatus.FAILED, StepStatus.CANCELLED}),
    StepStatus.WAITING: frozenset({StepStatus.RUNNING, StepStatus.CANCELLED, StepStatus.SKIPPED}),
    StepStatus.SKIPPED: frozenset(),
    StepStatus.CANCELLED: frozenset(),
    StepStatus.BLOCKED: frozenset({StepStatus.READY, StepStatus.SKIPPED, StepStatus.CANCELLED}),
}


def transition_step(current: StepStatus, new: StepStatus) -> StepStatus:
    if new not in LEGAL_STEP_TRANSITIONS[current]:
        raise IllegalExecutionTransition(f"illegal step transition {current.value} -> {new.value}")
    return new


class TerminalOutcome(StrEnum):
    """§93: structured terminal outcomes — never collapse into 'failed'."""

    SUCCESS = "SUCCESS"
    PARTIAL_SUCCESS = "PARTIAL_SUCCESS"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    BLOCKED = "BLOCKED"
    WAITING_HUMAN = "WAITING_HUMAN"
    DEAD_END = "DEAD_END"
    BUDGET_EXCEEDED = "BUDGET_EXCEEDED"


class RetryClass(StrEnum):
    """§25: structured error classification driving retry eligibility."""

    TRANSIENT = "TRANSIENT"
    RATE_LIMIT = "RATE_LIMIT"
    TIMEOUT = "TIMEOUT"
    NETWORK = "NETWORK"
    AUTH = "AUTH"
    POLICY = "POLICY"
    INVALID_INPUT = "INVALID_INPUT"
    SCHEMA = "SCHEMA"
    NOT_FOUND = "NOT_FOUND"
    SIDE_EFFECT_UNCERTAIN = "SIDE_EFFECT_UNCERTAIN"
    PERMANENT = "PERMANENT"
    BUG = "BUG"
    CANCELLED = "CANCELLED"
    UNKNOWN = "UNKNOWN"


#: Classes a retry can legitimately help (§28); everything else routes to
#: repair/fallback/human/terminate instead.
RETRYABLE_CLASSES: frozenset[RetryClass] = frozenset(
    {RetryClass.TRANSIENT, RetryClass.RATE_LIMIT, RetryClass.TIMEOUT, RetryClass.NETWORK}
)


class RetryPolicy(BaseModel):
    """§26: per-step retry policy."""

    model_config = ConfigDict(frozen=True)

    max_attempts: int = 3
    initial_delay_s: float = 0.5
    max_delay_s: float = 10.0
    backoff_factor: float = 2.0
    jitter: bool = True
    retryable_classes: frozenset[RetryClass] = RETRYABLE_CLASSES


class JoinPolicy(StrEnum):
    """§41: parallel-group completion semantics."""

    ALL_REQUIRED = "ALL_REQUIRED"
    ALL_BEST_EFFORT = "ALL_BEST_EFFORT"
    MIN_SUCCESS_COUNT = "MIN_SUCCESS_COUNT"
    MIN_SUCCESS_RATIO = "MIN_SUCCESS_RATIO"
    FIRST_SUCCESS = "FIRST_SUCCESS"


class ExecutionRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    plan: RoutePlan
    task_id: str
    correlation_id: str
    route_id: str = ""
    catalog_version: int = 0  # the routing snapshot this plan came from (§31)
    run_id: str | None = None  # set when resuming


class ExecutionAttempt(BaseModel):
    model_config = ConfigDict(frozen=True)

    attempt: int
    candidate_id: str
    started_at: datetime | None = None
    completed_at: datetime | None = None
    ok: bool = False
    error_class: RetryClass = RetryClass.UNKNOWN
    error: str | None = None
    duration_ms: int = 0


class ExecutionObservation(BaseModel):
    """§54: the normalized result of one step execution. Large outputs stay
    referenced (data_ref), never embedded wholesale (§53)."""

    model_config = ConfigDict(frozen=True)

    step_id: str
    ok: bool
    summary: str = ""
    data: Any = None
    data_ref: str | None = None
    error: str | None = None
    error_class: RetryClass = RetryClass.UNKNOWN
    provider: str = ""
    duration_ms: int = 0
    side_effects: tuple[str, ...] = ()
    metadata: dict[str, Any] = Field(default_factory=dict)


class StepRun(BaseModel):
    """Mutable-per-copy runtime state of ONE step (§8). Immutable snapshots:
    transitions produce a new StepRun via model_copy."""

    model_config = ConfigDict(frozen=True)

    step_id: str
    candidate_id: str | None
    status: StepStatus = StepStatus.PENDING
    attempt: int = 0
    observations: tuple[ExecutionObservation, ...] = ()
    last_failure_class: RetryClass | None = None
    last_failure: str | None = None
    fallback_cursor: int = 0  # position in the decision's fallback chain (§53 Part 3)
    started_at: datetime | None = None
    completed_at: datetime | None = None

    def transition(self, new: StepStatus) -> StepRun:
        return self.model_copy(update={"status": transition_step(self.status, new)})


class ExecutionRun(BaseModel):
    """§6: one durable execution run of a RoutePlan. References trajectories
    and events by id — raw transcripts never live here."""

    model_config = ConfigDict(frozen=True)

    run_id: str
    task_id: str
    correlation_id: str
    route_plan_id: str
    route_id: str = ""
    status: RunStatus = RunStatus.CREATED
    outcome: TerminalOutcome | None = None
    current_step: str | None = None
    steps: tuple[StepRun, ...] = Field(default_factory=tuple)
    attempt_count: int = 0
    replan_count: int = 0
    fallback_count: int = 0
    limits: dict[str, float] = Field(default_factory=dict)
    catalog_version: int = 0
    route_version: str = ""
    policy_snapshot_version: int = 1
    created_at: datetime | None = None
    updated_at: datetime | None = None
    completed_at: datetime | None = None
    error: str | None = None

    def step(self, step_id: str) -> StepRun | None:
        return next((s for s in self.steps if s.step_id == step_id), None)

    def transition(self, new: RunStatus) -> ExecutionRun:
        return self.model_copy(update={"status": transition_run(self.status, new)})


class ExecutionRunResult(BaseModel):
    """§94: the terminal result of a run."""

    model_config = ConfigDict(frozen=True)

    run_id: str
    task_id: str
    status: RunStatus
    outcome: TerminalOutcome
    summary: str = ""
    outputs: dict[str, Any] = Field(default_factory=dict)  # step_id -> observation data (small values)
    artifacts: tuple[dict[str, Any], ...] = ()  # artifact REFERENCES (§53)
    completed_steps: tuple[str, ...] = ()
    failed_steps: tuple[str, ...] = ()
    attempts: int = 0
    fallbacks: int = 0
    replans: int = 0
    duration_ms: int = 0
    provenance: tuple[str, ...] = ()


__all__ = [
    "RETRYABLE_CLASSES",
    "ExecutionAttempt",
    "ExecutionObservation",
    "ExecutionRequest",
    "ExecutionRun",
    "ExecutionRunResult",
    "IllegalExecutionTransition",
    "JoinPolicy",
    "RetryClass",
    "RetryPolicy",
    "RunStatus",
    "StepRun",
    "StepStatus",
    "TerminalOutcome",
    "transition_run",
    "transition_step",
]
