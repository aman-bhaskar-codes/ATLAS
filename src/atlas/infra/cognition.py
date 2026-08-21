"""Cognitive runtime contracts — the typed state of a thinking task.

WHY this module lives in ``atlas.infra``: the layer contract in
``importlinter.ini`` orders ``orchestration > capabilities > memory >
intelligence > safety > tools > infra``. A ``TaskIntent`` is produced at the
top of the pipeline but consumed all the way down (capability selection,
model routing, verification). Only a bottom-layer module can be imported by
every consumer without inverting the layering.

WHY strongly typed structures instead of model text: raw LLM output is never
the system's internal state. The model *proposes* — it emits JSON that we
parse into these contracts, validate, and then act on. Every field below is
something the runtime branches on or records; nothing here is decoration.

Naming note: ``Decision`` is already taken in ``atlas.infra.types`` for the
safety allow/deny literal. The per-step cognitive choice is therefore
``StepDecision`` — the audit found four separate name collisions in this
codebase already and this module does not add a fifth.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import IntEnum, StrEnum
from typing import Any, Literal, Protocol, Self

from pydantic import BaseModel, ConfigDict, Field

from atlas.infra.ids import CorrelationId, TaskId
from atlas.infra.types import Tier


class _Frozen(BaseModel):
    """Immutable value object. Transitions produce copies, never mutations."""

    model_config = ConfigDict(frozen=True)


# ---------------------------------------------------------------------------
# Classification vocabularies
# ---------------------------------------------------------------------------


class RiskLevel(StrEnum):
    """Task/plan risk. Canonical definition.

    WHY here and not in ``orchestration.types``: ``TaskIntent`` carries a risk
    level and lives in infra, so infra must own the enum. ``orchestration``
    re-exports this name for backward compatibility rather than redeclaring it.
    """

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class LatencyClass(StrEnum):
    """Declared responsiveness of a capability or model.

    WHY declared rather than measured: the planner needs this *before* it
    commits to a strategy. Measured latency lives in telemetry and is used to
    correct these labels over time, not to replace them.
    """

    INSTANT = "instant"  # < 100ms, in-process
    FAST = "fast"  # < 2s, local or cached
    MODERATE = "moderate"  # < 10s, single network round trip
    SLOW = "slow"  # < 60s, multi-step or heavy model
    VERY_SLOW = "very_slow"  # minutes; must be backgrounded


class ModelTier(StrEnum):
    """Which inference tier a call belongs to (Phase 4).

    FAST covers classification, structured extraction, routing, action
    formatting, short observations, low-risk decisions and summaries.
    DEEP covers hard planning, ambiguity, multi-step reasoning, recovery,
    replanning, difficult verification and research synthesis.
    """

    FAST = "fast"
    DEEP = "deep"


class ReasoningLevel(IntEnum):
    """Bounded reasoning effort, selected per task (Phase 10).

    WHY an IntEnum: the selector compares levels (``level >= L2``) and the
    level indexes step/token budgets, so ordering must be meaningful.
    """

    L0_DIRECT = 0  # deterministic; no planning, no model reasoning
    L1_SIMPLE = 1  # single-step plan, one tool at most
    L2_MULTI_STEP = 2  # multi-step plan, tool chaining
    L3_DEEP = 3  # deep model + mandatory verification
    L4_RESEARCH = 4  # iterative research, cross-source synthesis


class TaskDomain(StrEnum):
    """Coarse subject area. Drives verifier choice and capability retrieval."""

    CODING = "coding"
    FILESYSTEM = "filesystem"
    RESEARCH = "research"
    COMMUNICATION = "communication"
    SCHEDULING = "scheduling"
    SYSTEM = "system"
    SELF_KNOWLEDGE = "self_knowledge"  # questions about ATLAS itself
    CONVERSATION = "conversation"  # no side effects, no tools
    UNKNOWN = "unknown"


class Urgency(StrEnum):
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"


class Complexity(StrEnum):
    TRIVIAL = "trivial"
    SIMPLE = "simple"
    MODERATE = "moderate"
    COMPLEX = "complex"


# ---------------------------------------------------------------------------
# TaskIntent — built ONCE per task, consumed everywhere (Phase 2)
# ---------------------------------------------------------------------------


class TaskIntent(_Frozen):
    """Structured understanding of a user request.

    WHY exactly one construction site: the audit found the existing router
    re-derives a 7-flag ``Capabilities`` bag whose output is ~70% unread, while
    downstream stages each re-guess what the user wanted. This object is built
    once by the understanding stage and then *read* by capability selection,
    model routing, planning, verification and recording.
    """

    objective: str
    domain: TaskDomain = TaskDomain.UNKNOWN
    constraints: tuple[str, ...] = ()
    success_criteria: tuple[str, ...] = ()
    risk: RiskLevel = RiskLevel.LOW
    privacy_level: str = "internal"  # PrivacyClass value; string to stay leaf
    urgency: Urgency = Urgency.NORMAL
    complexity: Complexity = Complexity.SIMPLE
    required_capabilities: tuple[str, ...] = ()
    likely_side_effects: tuple[str, ...] = ()
    verification_requirements: tuple[str, ...] = ()
    reasoning_level: ReasoningLevel = ReasoningLevel.L1_SIMPLE
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)

    def needs_verification(self) -> bool:
        """WHY criteria-driven: the audit found verification was a no-op
        precisely because nothing ever populated success criteria. If an intent
        declares criteria or requirements, verification is mandatory."""
        return bool(self.success_criteria or self.verification_requirements)

    def needs_tools(self) -> bool:
        return bool(self.required_capabilities) or self.domain not in (
            TaskDomain.CONVERSATION,
            TaskDomain.UNKNOWN,
        )


# ---------------------------------------------------------------------------
# GoalState — desired vs current state (Phase 1)
# ---------------------------------------------------------------------------


class GoalState(_Frozen):
    """Evolving understanding of success for one task.

    WHY frozen with copy helpers: the previous implementation was a mutable
    dataclass shared by reference between the orchestrator and the reasoning
    loop, so no caller could tell which component had advanced progress. Copies
    make every transition explicit and safe to serialize into a checkpoint.
    """

    objective: str
    constraints: tuple[str, ...] = ()
    success_criteria: tuple[str, ...] = ()
    current_state: str = "not_started"
    progress: float = Field(default=0.0, ge=0.0, le=1.0)
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    replan_count: int = 0
    max_replans: int = 3
    created_ts: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @classmethod
    def from_intent(cls, intent: TaskIntent, *, max_replans: int = 3) -> Self:
        """Single bridge from understanding to goal tracking.

        WHY this exists: success criteria must reach the verifier. The audit
        proved that a ``GoalState`` built without criteria makes verification
        return ``passed=True`` unconditionally.
        """
        return cls(
            objective=intent.objective,
            constraints=intent.constraints,
            success_criteria=intent.success_criteria,
            confidence=intent.confidence,
            max_replans=max_replans,
        )

    def can_replan(self) -> bool:
        return self.replan_count < self.max_replans

    def with_replan(self) -> Self:
        return self.model_copy(update={"replan_count": self.replan_count + 1})

    def with_progress(self, progress: float, current_state: str = "") -> Self:
        update: dict[str, Any] = {"progress": max(0.0, min(1.0, progress))}
        if current_state:
            update["current_state"] = current_state
        return self.model_copy(update=update)

    def with_confidence(self, confidence: float) -> Self:
        return self.model_copy(update={"confidence": max(0.0, min(1.0, confidence))})

    def to_prompt_fragment(self) -> str:
        lines = [
            f"Objective: {self.objective}",
            f"Progress: {int(self.progress * 100)}%  Confidence: {int(self.confidence * 100)}%",
        ]
        if self.success_criteria:
            lines.append("Success when: " + "; ".join(self.success_criteria))
        if self.constraints:
            lines.append("Constraints: " + "; ".join(self.constraints))
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Action — the single dispatchable, auditable action (Phase 29)
# ---------------------------------------------------------------------------

ActionSource = Literal["plan", "llm", "recovery", "user", "system"]
StepKind = Literal["act", "final_answer", "ask_user", "noop"]


class Action(_Frozen):
    """One thing ATLAS intends to do, fully described before it is allowed.

    WHY ``capability`` rather than ``tool``: Phase 9 unifies tool and
    capability execution onto one path, so the dispatch key is a capability
    name. Legacy tool names are valid capability names, which keeps existing
    tools working unchanged.

    WHY ``requires_approval`` is only a *hint*: the authoritative decision is
    the SafetyEngine's. A model may not lower it, and the engine may raise it.
    """

    action_id: str
    capability: str
    operation: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    risk: RiskLevel = RiskLevel.LOW
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    reversible: bool = False
    requires_approval: bool = False
    source: ActionSource = "llm"
    reason_summary: str = ""  # structured summary, never raw chain-of-thought
    declared_tier_hint: Tier | None = None
    step: int = 0


class StepDecision(_Frozen):
    """What the reasoning loop chose to do at one step.

    WHY separate from ``Action``: ending the task and taking an action are
    different kinds of choice. Collapsing them forced the old loop to invent a
    ``kind="final_answer"`` action with null tool fields.
    """

    step: int
    kind: StepKind
    action: Action | None = None
    final_text: str | None = None
    rationale_summary: str = ""
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    reasoning_level: ReasoningLevel = ReasoningLevel.L1_SIMPLE

    def model_post_init(self, _ctx: object, /) -> None:
        # WHY validate here: an "act" decision with no action is the exact
        # shape a malformed model response produces, and it must not reach
        # the dispatcher as a silent no-op.
        if self.kind == "act" and self.action is None:
            raise ValueError("StepDecision(kind='act') requires an action")


# ---------------------------------------------------------------------------
# Observation — bounded, structured result of acting (Phase 14)
# ---------------------------------------------------------------------------


class ObservationStatus(StrEnum):
    OK = "ok"
    FAILED = "failed"
    DENIED = "denied"  # SafetyEngine refused
    HALTED = "halted"  # kill switch / cancellation
    TIMEOUT = "timeout"
    SKIPPED = "skipped"


class Observation(_Frozen):
    """Structured record of what happened, safe to put in a prompt.

    WHY ``result_summary`` is separate from ``result``: the audit found raw
    tool output was interpolated into every subsequent prompt with a blunt
    ``[:300]`` truncation. The summary is what the model sees; the full result
    stays available to verifiers and the trajectory store without paying for
    context on every step.
    """

    step: int
    capability: str
    operation: str
    status: ObservationStatus
    result_summary: str = ""
    result: Any = None
    error: str | None = None
    error_code: str | None = None
    retryable: bool = False
    latency_ms: int = 0
    occurred_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    provenance: tuple[str, ...] = ()  # sources/files/URLs this result came from
    side_effects: tuple[str, ...] = ()
    verification_relevant: bool = False

    @property
    def ok(self) -> bool:
        """Backward-compatible accessor; existing call sites read ``.ok``."""
        return self.status is ObservationStatus.OK


# ---------------------------------------------------------------------------
# Verification (Phase 12)
# ---------------------------------------------------------------------------


class CriterionResult(_Frozen):
    criterion: str
    passed: bool
    detail: str = ""
    evidence: tuple[str, ...] = ()


class VerificationResult(_Frozen):
    """Outcome of checking work against the goal.

    WHY ``verifier`` is recorded: Phase 12 requires capability-aware
    verification, so the report must say which strategy ran. A result with
    ``verifier="none"`` is a signal that nothing was actually checked, which
    the previous implementation silently reported as a pass.
    """

    passed: bool
    score: float = Field(default=0.0, ge=0.0, le=1.0)
    verifier: str = "none"
    criteria_results: tuple[CriterionResult, ...] = ()
    failure_reason: str | None = None
    suggested_next_action: str | None = None
    evidence: tuple[str, ...] = ()
    latency_ms: int = 0

    @classmethod
    def not_applicable(cls, reason: str) -> Self:
        """Explicitly not verified — distinct from verified-and-passed.

        WHY: ``passed=True, score=1.0`` for an unverified task is precisely the
        false signal that made every recorded trajectory claim success.
        """
        return cls(passed=True, score=0.0, verifier="none", failure_reason=reason)

    @classmethod
    def error(cls, reason: str, *, verifier: str) -> Self:
        """Verifier crashed. Fails CLOSED.

        WHY closed: the old implementation caught bare ``Exception`` and
        returned ``passed=True``, making a broken verifier indistinguishable
        from a genuine pass.
        """
        return cls(
            passed=False,
            score=0.0,
            verifier=verifier,
            failure_reason=f"verifier_error: {reason}",
            suggested_next_action="retry_verification",
        )

    def to_prompt_fragment(self) -> str:
        if self.passed:
            return f"Verification[{self.verifier}]: PASSED (score {self.score:.2f})"
        parts = [
            f"Verification[{self.verifier}]: FAILED (score {self.score:.2f})",
            f"Reason: {self.failure_reason or 'unknown'}",
        ]
        failed = [c.criterion for c in self.criteria_results if not c.passed]
        if failed:
            parts.append("Unmet: " + "; ".join(failed))
        if self.suggested_next_action:
            parts.append(f"Suggested: {self.suggested_next_action}")
        return "\n".join(parts)


# ---------------------------------------------------------------------------
# ExecutionContext / ExecutionResult (Phase 1)
# ---------------------------------------------------------------------------


class Evidence(_Frozen):
    """A bounded, already-summarised fact a verifier may rely on.

    WHY not pass ``Observation`` objects to verifiers: Phase 14 forbids moving
    raw tool output around, and the observation type lives in the
    ``orchestration`` layer, which infra cannot import. The reasoning loop
    projects its observations onto this small record, so verifiers get citable
    provenance without the layer inversion or the token cost.
    """

    source: str  # capability name, file path, or URL
    operation: str = ""
    ok: bool = True
    summary: str = ""  # producer is responsible for truncating this


class Verifier(Protocol):
    """Checks whether produced work satisfies the goal.

    WHY a protocol: Phase 12 requires capability-aware verification — a coding
    task verifies by running tests, a filesystem task by comparing resulting
    state, a research task by cross-checking sources. They share this contract
    so the reasoning loop never branches on domain itself.

    WHY ``correlation_id`` is required: verification makes model and tool calls
    that must be attributable to the task that triggered them. The previous
    implementation used a literal ``"verification"`` id, so verification cost
    could not be joined to any task.
    """

    name: str

    async def verify(
        self,
        goal: GoalState,
        answer: str,
        correlation_id: CorrelationId,
        context: str = "",
        domain: TaskDomain = TaskDomain.UNKNOWN,
        evidence: tuple[Evidence, ...] = (),
    ) -> VerificationResult: ...


class ExecutionContext(_Frozen):
    """Immutable per-task identity and budget envelope.

    WHY this exists: the audit found ``correlation_id`` doubling as
    ``task_id`` in safety events, a literal ``"verification"`` correlation id
    in the verifier, and a literal ``"fallback"`` in fallback events — so
    events could not be joined back to their task. Threading one context
    object removes the temptation to invent identifiers locally.
    """

    task_id: TaskId
    correlation_id: CorrelationId
    intent: TaskIntent
    reasoning_level: ReasoningLevel = ReasoningLevel.L1_SIMPLE
    max_steps: int = 15
    max_replans: int = 3
    token_budget: int = 40_000
    deadline_ms: int | None = None
    started_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    def child_step(self, step: int) -> str:
        """Stable per-step id for telemetry joins (``llm_calls.step_index``)."""
        return f"{self.task_id}:{step}"


class ExecutionResult(_Frozen):
    """Terminal outcome of one task, with the evidence that justifies it."""

    task_id: TaskId
    ok: bool
    answer: str | None = None
    intent: TaskIntent | None = None
    verification: VerificationResult | None = None
    observations: tuple[Observation, ...] = ()
    steps_taken: int = 0
    replan_count: int = 0
    model_calls: int = 0
    tool_calls: int = 0
    tokens_used: int = 0
    latency_ms: int = 0
    error: str | None = None
    error_code: str | None = None
    reasoning_level: ReasoningLevel = ReasoningLevel.L1_SIMPLE
