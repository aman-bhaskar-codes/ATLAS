"""Typed learning objects for the adaptation plane (Prompt 4 §1).

Everything here is an explicit pydantic model — never an arbitrary JSON blob.
Objects that already exist in the runtime (Trajectory, DecisionTrace,
Experience, Skill, Strategy) are reused/extended in place; this module adds
the adaptation-level objects the runtime does not know about: evaluations,
failure analyses, hypotheses, experiments, comparisons, generalizations,
promotions, adaptation points and decision preferences.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from atlas.adaptation.taxonomy import FailureClass

# ---------------------------------------------------------------------------
# Learning state machine (§100)


class LearningState(StrEnum):
    """Global learning-cycle states."""

    IDLE = "IDLE"
    OBSERVING = "OBSERVING"
    DIAGNOSING = "DIAGNOSING"
    HYPOTHESIZING = "HYPOTHESIZING"
    EXPERIMENTING = "EXPERIMENTING"
    EVALUATING = "EVALUATING"
    GENERALIZING = "GENERALIZING"
    PROMOTING = "PROMOTING"
    ROLLED_BACK = "ROLLED_BACK"


# ---------------------------------------------------------------------------
# Outcome evaluation (§4)


class TrajectoryEvaluation(BaseModel):
    """Dimension scores for one trajectory (§4). Missing dimensions are None —
    different task types use different evaluator subsets."""

    model_config = ConfigDict(frozen=True)

    trajectory_id: str
    goal_achievement: float | None = None
    correctness: float | None = None
    completeness: float | None = None
    verification: float | None = None
    safety: float | None = None
    efficiency: float | None = None
    latency: float | None = None
    cost: float | None = None
    tool_selection: float | None = None
    planning_quality: float | None = None
    recovery_quality: float | None = None
    knowledge_grounding: float | None = None
    citation_quality: float | None = None
    memory_usefulness: float | None = None
    user_feedback: float | None = None
    evaluator_levels: tuple[int, ...] = ()  # which hierarchy levels scored this
    created_ts: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())

    def scores(self) -> dict[str, float]:
        """Only the dimensions actually scored (non-None)."""
        return {
            name: value
            for name in (
                "goal_achievement",
                "correctness",
                "completeness",
                "verification",
                "safety",
                "efficiency",
                "latency",
                "cost",
                "tool_selection",
                "planning_quality",
                "recovery_quality",
                "knowledge_grounding",
                "citation_quality",
                "memory_usefulness",
                "user_feedback",
            )
            if (value := getattr(self, name)) is not None
        }


class EvaluationVerdict(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"
    INCONCLUSIVE = "INCONCLUSIVE"


class OutcomeEvaluation(BaseModel):
    """Aggregated verdict for one trajectory, produced by the evaluator
    hierarchy (§5)."""

    model_config = ConfigDict(frozen=True)

    trajectory_id: str
    verdict: EvaluationVerdict
    overall_score: float
    dimensions: TrajectoryEvaluation
    rationale: str = ""
    created_ts: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())


# ---------------------------------------------------------------------------
# Failure analysis (§7)


class FailureAnalysis(BaseModel):
    """Symptom-vs-root-cause analysis for one failed trajectory."""

    model_config = ConfigDict(frozen=True)

    trajectory_id: str
    primary_cause: FailureClass
    secondary_causes: tuple[FailureClass, ...] = ()
    evidence: tuple[str, ...] = ()
    confidence: float = 0.0
    avoidable: bool = False
    recommended_intervention: str = ""
    created_ts: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())


# ---------------------------------------------------------------------------
# Strategy versioning (§13) and performance (§14)


class StrategyVersion(BaseModel):
    """Immutable strategy definition. Never overwritten in place — a new
    version row is created instead."""

    model_config = ConfigDict(frozen=True)

    strategy_id: str
    version: int
    definition: str
    task_type_pattern: str = "*"
    skills: tuple[str, ...] = ()
    retrieval_policy: str = ""
    model_preference: str = ""
    tool_preference: str = ""
    verification_policy: str = ""
    created_ts: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    change_reason: str = ""
    source_experiments: tuple[str, ...] = ()


class StrategyPerformance(BaseModel):
    """§14: success rate alone is never enough."""

    model_config = ConfigDict(frozen=True)

    strategy_id: str
    version: int
    runs: int = 0
    success_rate: float = 0.0
    quality_score: float = 0.0
    latency_ms_avg: float = 0.0
    cost_usd_avg: float = 0.0
    recovery_rate: float = 0.0
    verification_rate: float = 0.0
    generalization: float = 0.0
    user_feedback: float = 0.0
    updated_ts: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())


# ---------------------------------------------------------------------------
# Change types (§18)


class AllowedChangeType(StrEnum):
    """What adaptation MAY modify."""

    STRATEGY = "STRATEGY"
    SKILL = "SKILL"
    MODEL_ROUTING = "MODEL_ROUTING"
    TOOL_RANKING = "TOOL_RANKING"
    RETRIEVAL_WEIGHTS = "RETRIEVAL_WEIGHTS"
    RERANKER_CHOICE = "RERANKER_CHOICE"
    QUERY_REWRITING = "QUERY_REWRITING"
    SOURCE_PREFERENCE = "SOURCE_PREFERENCE"
    VERIFICATION_ORDERING = "VERIFICATION_ORDERING"
    CONTEXT_COMPILATION = "CONTEXT_COMPILATION"
    WORKFLOW_ORDERING = "WORKFLOW_ORDERING"


class ForbiddenChangeType(StrEnum):
    """What adaptation may NEVER modify — human controlled, always (§18/§98)."""

    SAFETY_ENGINE = "SAFETY_ENGINE"
    PERMISSION_POLICIES = "PERMISSION_POLICIES"
    CREDENTIAL_RULES = "CREDENTIAL_RULES"
    SANDBOX_SECURITY = "SANDBOX_SECURITY"
    AUDIT_REQUIREMENTS = "AUDIT_REQUIREMENTS"


# ---------------------------------------------------------------------------
# Hypotheses (§16-17)

MIN_EVIDENCE_DEFAULT = 3  # §16: minimum repeated evidence


class HypothesisStatus(StrEnum):
    PROPOSED = "PROPOSED"
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    EVALUATED = "EVALUATED"
    PROMOTABLE = "PROMOTABLE"
    REJECTED = "REJECTED"
    PROMOTED = "PROMOTED"
    ROLLED_BACK = "ROLLED_BACK"


class Hypothesis(BaseModel):
    """A testable claim generated from repeated measured evidence."""

    model_config = ConfigDict(frozen=True)

    hypothesis_id: str = Field(default_factory=lambda: f"hyp_{uuid.uuid4().hex[:12]}")
    title: str
    problem_statement: str
    evidence: tuple[str, ...] = ()  # trajectory/failure/feedback ids backing it
    affected_component: str
    proposed_change: str
    change_type: AllowedChangeType
    expected_effect: str = ""
    risk: Literal["LOW", "MEDIUM", "HIGH"] = "LOW"
    constraints: tuple[str, ...] = ()
    evaluation_plan: str = ""
    status: HypothesisStatus = HypothesisStatus.PROPOSED
    experiment_id: str | None = None
    created_ts: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    updated_ts: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())


# ---------------------------------------------------------------------------
# Experiments (§19-22)


class ExperimentStatus(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    ABORTED = "ABORTED"
    BUDGET_LIMITED = "BUDGET_LIMITED"  # §86: aborted safely, kept as evidence


class ExperimentArm(BaseModel):
    """One side of a controlled experiment."""

    model_config = ConfigDict(frozen=True)

    arm: Literal["BASELINE", "CANDIDATE"]
    strategy_version: str | None = None
    model: str | None = None
    configuration: dict[str, Any] = Field(default_factory=dict)
    metrics: dict[str, float] = Field(default_factory=dict)  # filled after run
    n_tasks: int = 0


class ResourceLimits(BaseModel):
    """§19: every experiment has hard resource limits."""

    model_config = ConfigDict(frozen=True)

    max_tasks: int = 50
    max_tokens: int = 200_000
    max_cost_usd: float = 5.0
    max_duration_seconds: float = 1800.0


class Experiment(BaseModel):
    """baseline → candidate → same benchmark → compare → generalize → safety."""

    model_config = ConfigDict(frozen=True)

    experiment_id: str = Field(default_factory=lambda: f"exp_{uuid.uuid4().hex[:12]}")
    hypothesis_id: str
    baseline: ExperimentArm
    candidate: ExperimentArm
    dataset_version: str
    pipeline_version: str
    atlas_version: str
    metrics: tuple[str, ...] = ("success_rate", "quality_score", "latency_ms", "cost_usd")
    resource_limits: ResourceLimits = Field(default_factory=ResourceLimits)
    status: ExperimentStatus = ExperimentStatus.PENDING
    created_ts: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    completed_ts: str | None = None


class ComparisonResult(BaseModel):
    """§21-22: baseline discipline + statistics appropriate to the data size."""

    model_config = ConfigDict(frozen=True)

    experiment_id: str
    metric: str
    baseline_version: str
    candidate_version: str
    dataset_version: str
    model_version: str = ""
    atlas_version: str
    n: int
    baseline_mean: float
    candidate_mean: float
    baseline_median: float | None = None
    candidate_median: float | None = None
    baseline_variance: float | None = None
    candidate_variance: float | None = None
    confidence_interval_low: float | None = None  # CI for the candidate-baseline delta
    confidence_interval_high: float | None = None
    effect_size: float | None = None
    paired: bool = False  # same tasks scored under both arms
    significant: bool = False  # only meaningful when n is large enough


# ---------------------------------------------------------------------------
# Generalization (§38-39)


class GeneralizationResult(BaseModel):
    """Did the improvement survive tasks the experiment did not train on?"""

    model_config = ConfigDict(frozen=True)

    experiment_id: str
    baseline_score: float
    candidate_score: float
    n_tasks: int
    holds_on_unseen: bool
    score_by_domain: dict[str, float] = Field(default_factory=dict)
    created_ts: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())


# ---------------------------------------------------------------------------
# Promotion (§23-24)


class PromotionState(StrEnum):
    PROPOSED = "PROPOSED"
    TESTING = "TESTING"
    VALIDATED = "VALIDATED"
    CANDIDATE = "CANDIDATE"
    PROMOTED = "PROMOTED"
    REJECTED = "REJECTED"
    ROLLED_BACK = "ROLLED_BACK"


class PromotionDecision(BaseModel):
    """Outcome of applying the PromotionPolicy. SAFETY REGRESSION = REJECT
    always — the policy hard-fails before any other criterion is consulted."""

    model_config = ConfigDict(frozen=True)

    experiment_id: str
    hypothesis_id: str
    decision: Literal["PROMOTE", "REJECT", "HOLD"]
    reasons: tuple[str, ...] = ()
    safety_regression: bool = False
    promotion_state: PromotionState = PromotionState.PROPOSED
    promoted_strategy_id: str | None = None
    promoted_version: int | None = None
    created_ts: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())


# ---------------------------------------------------------------------------
# Adaptation points & decision preferences (§35-37, §69)


class AdaptationPoint(StrEnum):
    """Runtime decision sites that adaptation may tune (all allowed change
    types map onto one of these; safety sites are NOT adaptation points)."""

    MODEL_SELECTION = "MODEL_SELECTION"
    TOOL_SELECTION = "TOOL_SELECTION"
    STRATEGY_SELECTION = "STRATEGY_SELECTION"
    RETRIEVAL_MODE = "RETRIEVAL_MODE"
    SOURCE_SELECTION = "SOURCE_SELECTION"
    QUERY_REWRITE = "QUERY_REWRITE"
    RERANKER_SELECTION = "RERANKER_SELECTION"
    VERIFICATION_ORDERING = "VERIFICATION_ORDERING"
    CONTEXT_COMPILATION = "CONTEXT_COMPILATION"


class DecisionPreference(BaseModel):
    """A learned preference at one adaptation point, backed by measured
    evidence. Routing consults these; nothing here can override safety."""

    model_config = ConfigDict(frozen=True)

    preference_id: str = Field(default_factory=lambda: f"pref_{uuid.uuid4().hex[:12]}")
    adaptation_point: AdaptationPoint
    context_key: str = ""  # e.g. task_type pattern or domain
    preferred_option: str
    evidence_count: int = 0
    success_rate: float = 0.0
    source_experiment: str | None = None
    active: bool = True
    created_ts: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())


# ---------------------------------------------------------------------------
# Shadow mode (§25)


class ShadowVerdict(StrEnum):
    EQUIVALENT = "EQUIVALENT"
    CANDIDATE_BETTER = "CANDIDATE_BETTER"
    CANDIDATE_WORSE = "CANDIDATE_WORSE"


class ShadowDecision(BaseModel):
    """What the candidate strategy says it WOULD have done for a task the
    active strategy already handled for real (§25)."""

    model_config = ConfigDict(frozen=True)

    decision: str = ""
    plan: tuple[str, ...] = ()
    tool_choice: str = ""
    retrieval: tuple[str, ...] = ()
    expected_result: float = 0.0  # predicted quality 0..1


class ShadowComparison(BaseModel):
    """Active-vs-candidate comparison for one trajectory (§25)."""

    model_config = ConfigDict(frozen=True)

    comparison_id: str = Field(default_factory=lambda: f"shdw_{uuid.uuid4().hex[:12]}")
    trajectory_id: str
    strategy_id: str
    baseline_version: int
    candidate_version: int
    decision_agreement: float = 0.0
    plan_similarity: float = 0.0
    tool_choice_agreement: float = 0.0
    retrieval_similarity: float = 0.0
    expected_result_delta: float = 0.0
    verdict: ShadowVerdict = ShadowVerdict.EQUIVALENT
    created_ts: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())


# ---------------------------------------------------------------------------
# Canary adaptation (§26)


class CanaryStatus(StrEnum):
    SHADOWING = "SHADOWING"
    CANARY = "CANARY"
    EXPANDING = "EXPANDING"
    FULL = "FULL"
    ROLLED_BACK = "ROLLED_BACK"


class CanaryDeployment(BaseModel):
    """A graduated rollout of one strategy version (§26: 5/10/25/50/100%)."""

    model_config = ConfigDict(frozen=True)

    deployment_id: str = Field(default_factory=lambda: f"cnry_{uuid.uuid4().hex[:12]}")
    strategy_id: str
    version: int
    percentage: float = 5.0
    status: CanaryStatus = CanaryStatus.CANARY
    tasks_seen: int = 0
    created_ts: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    updated_ts: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())


class CanaryObservation(BaseModel):
    """One live outcome observed while the canary handled a task (§26)."""

    model_config = ConfigDict(frozen=True)

    deployment_id: str
    trajectory_id: str
    success: bool = False
    regression: bool = False
    safety_event: bool = False
    latency_ms: float = 0.0
    cost_usd: float = 0.0
    ts: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())


# ---------------------------------------------------------------------------
# Counterfactual learning (§27-§30)


class CounterfactualMode(StrEnum):
    """How the alternative outcome was obtained. Every mode is side-effect
    free (§28) — real external mutations are never replayed."""

    DETERMINISTIC = "DETERMINISTIC"
    SANDBOX = "SANDBOX"
    GOLDEN = "GOLDEN"
    RECORDED = "RECORDED"
    SIMULATION = "SIMULATION"
    DRY_RUN = "DRY_RUN"


class CounterfactualResult(BaseModel):
    """"What would have happened if ATLAS had chosen differently?" (§27)."""

    model_config = ConfigDict(frozen=True)

    counterfactual_id: str = Field(default_factory=lambda: f"cf_{uuid.uuid4().hex[:12]}")
    trajectory_id: str
    adaptation_point: AdaptationPoint
    original_option: str
    alternative_option: str
    original_outcome: str  # DecisionOutcome value of the original choice
    alternative_outcome: str = ""  # measured/simulated outcome of the alternative
    mode: CounterfactualMode = CounterfactualMode.SIMULATION
    delta: float = 0.0  # alternative score minus original score
    created_ts: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())


# ---------------------------------------------------------------------------
# Decision quality (§31)


class DecisionQuality(BaseModel):
    """Retrospective quality of one class of runtime decision (§31)."""

    model_config = ConfigDict(frozen=True)

    trajectory_id: str
    dimension: str  # model_selection | tool_selection | strategy | retrieval | verification | recovery
    score: float
    evidence: tuple[str, ...] = ()
    better_alternative: str = ""
    confidence: float = 0.0
    created_ts: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())


__all__ = [
    "MIN_EVIDENCE_DEFAULT",
    "AdaptationPoint",
    "AllowedChangeType",
    "CanaryDeployment",
    "CanaryObservation",
    "CanaryStatus",
    "ComparisonResult",
    "CounterfactualMode",
    "CounterfactualResult",
    "DecisionPreference",
    "DecisionQuality",
    "EvaluationVerdict",
    "Experiment",
    "ExperimentArm",
    "ExperimentStatus",
    "FailureAnalysis",
    "ForbiddenChangeType",
    "GeneralizationResult",
    "Hypothesis",
    "HypothesisStatus",
    "LearningState",
    "OutcomeEvaluation",
    "PromotionDecision",
    "PromotionState",
    "ResourceLimits",
    "StrategyPerformance",
    "StrategyVersion",
    "TrajectoryEvaluation",
]
