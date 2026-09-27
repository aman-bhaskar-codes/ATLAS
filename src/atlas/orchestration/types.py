"""Orchestration contracts.

WHY frozen models: Task/Plan/Action cross the whole pipeline and, for
checkpointing, are serialized. Immutability makes state transitions explicit
(you produce a new Task, you don't mutate one) and serialization safe.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field

from atlas.infra.cognition import RiskLevel
from atlas.infra.ids import CorrelationId, TaskId
from atlas.infra.types import Source

# WHY re-exported instead of redeclared: TaskIntent (infra) carries a risk
# level, so infra owns the enum. Existing imports of
# ``atlas.orchestration.types.RiskLevel`` keep working against the one
# canonical definition.
__all__ = [
    "Action",
    "ActionKind",
    "Capabilities",
    "Critique",
    "CritiqueVerdict",
    "Observation",
    "Plan",
    "PlanStep",
    "RiskLevel",
    "Task",
    "TaskResult",
    "Thought",
    "TokenUsage",
]


class Capabilities(BaseModel):
    """Router output: WHAT the task needs, not WHICH model/tool."""

    model_config = {"frozen": True}
    needs_memory: bool = True
    needs_retrieval: bool = True
    needs_tools: bool = False
    needs_reasoning: bool = True
    needs_confirmation: bool = False
    needs_cloud: bool = False
    max_risk: RiskLevel = RiskLevel.LOW


class PlanStep(BaseModel):
    model_config = {"frozen": True}
    index: int
    intent: str  # human-readable sub-goal
    tool: str | None = None  # suggested tool name (registry resolves)
    operation: str | None = None
    args: dict[str, Any] = Field(default_factory=dict)
    depends_on: tuple[int, ...] = ()  # step indices; enables DAG mode (Phase 12)
    expected_output: str | None = None


class Plan(BaseModel):
    model_config = {"frozen": True}
    goal: str
    constraints: tuple[str, ...] = ()
    steps: tuple[PlanStep, ...] = ()
    termination_conditions: tuple[str, ...] = ()
    risk: RiskLevel = RiskLevel.LOW
    estimated_cost_usd: float = 0.0
    confidence: float = 0.5
    unknowns: tuple[str, ...] = ()


class Thought(BaseModel):
    model_config = {"frozen": True}
    step: int
    content: str
    confidence: float = 0.5
    reasoning_details: str | None = None


ActionKind = Literal["tool_call", "final_answer", "ask_user", "noop"]


class Action(BaseModel):
    model_config = {"frozen": True}
    step: int
    kind: ActionKind
    tool: str | None = None
    operation: str | None = None
    args: dict[str, Any] = Field(default_factory=dict)
    final_text: str | None = None  # for final_answer / ask_user


class TokenUsage(BaseModel):
    """Per-step token consumption reported by an LLM call.

    Used by the DAG executor to feed LimitCounter.add_tokens() so that
    max_tokens actually trips.  ``total`` is the value ticked against the
    budget.
    """

    model_config = {"frozen": True}
    input_tokens: int = 0
    output_tokens: int = 0

    @property
    def total(self) -> int:
        return self.input_tokens + self.output_tokens


class Observation(BaseModel):
    """Result of executing a single plan step.

    ``usage`` is optional: *None* means token consumption is **unmeasured**
    (the step involved no LLM call, or the tool did not report usage).
    Unmeasured usage counts as 0 against the token budget — the step and
    tool-call limits still bound the run independently.
    """

    model_config = {"frozen": True}
    step: int
    ok: bool
    content: Any = None
    error: str | None = None
    usage: TokenUsage | None = None


class TaskResult(BaseModel):
    model_config = {"frozen": True}
    task_id: TaskId
    ok: bool
    answer: str | None = None
    steps_taken: int = 0
    error: str | None = None
    replan_count: int = 0  # Phase 1
    verification_passed: bool | None = None  # Phase 1
    verification_score: float | None = None  # Phase 1
    # Phase 2: Trajectory data for Orchestrator to save
    actions: tuple[Any, ...] = ()  # ActionRecord list
    observations: tuple[Any, ...] = ()  # ObservationRecord list
    decision_traces: tuple[Any, ...] = ()  # Phase 3: DecisionTrace list
    failure_records: tuple[Any, ...] = ()  # Phase 3: FailureRecord list
    latency_ms: int = 0  # Total execution time
    tokens_used: int = 0  # Total tokens
    model_calls: int = 0  # LLM invocations
    tool_calls: int = 0  # Tool invocations


class Task(BaseModel):
    """The unit of work. Immutable; transitions produce copies via model_copy."""

    model_config = {"frozen": True}
    id: TaskId
    correlation_id: CorrelationId
    source: Source
    request: str
    created_ts: datetime
    max_risk: RiskLevel = RiskLevel.LOW


class CritiqueVerdict(StrEnum):
    OK = "ok"  # action is sound — proceed (tier UNCHANGED)
    REVISE = "revise"  # regenerate once with the critique in mind
    ABORT = "abort"  # do not attempt; surface reason to the user


class Critique(BaseModel):
    model_config = {"frozen": True}
    verdict: CritiqueVerdict
    reason: str
    suggestion: str | None = None  # guidance for the revise pass
