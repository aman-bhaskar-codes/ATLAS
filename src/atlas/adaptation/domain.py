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
    """ "What would have happened if ATLAS had chosen differently?" (§27)."""

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


# ---------------------------------------------------------------------------
# Cognitive telemetry (§32)


class CognitiveTelemetry(BaseModel):
    """Per-trajectory cognitive dimension scores (§32). Missing dimensions
    are None — every trajectory only scores what it actually exercised."""

    model_config = ConfigDict(frozen=True)

    trajectory_id: str
    planning_quality: float | None = None
    tool_selection_accuracy: float | None = None
    model_selection_quality: float | None = None
    retrieval_usefulness: float | None = None
    memory_usefulness: float | None = None
    verification_quality: float | None = None
    recovery_quality: float | None = None
    research_efficiency: float | None = None
    strategy_transfer: float | None = None
    confidence_calibration: float | None = None
    created_ts: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())


# ---------------------------------------------------------------------------
# Confidence calibration (§33)


class CalibrationBucket(BaseModel):
    """One confidence bucket of the reliability curve (§33)."""

    model_config = ConfigDict(frozen=True)

    index: int
    n: int = 0
    mean_confidence: float = 0.0
    success_rate: float = 0.0


class CalibrationReport(BaseModel):
    """Expected calibration error + reliability curve (§33)."""

    model_config = ConfigDict(frozen=True)

    n_records: int = 0
    calibration_error: float = 0.0  # ECE: weighted |confidence - success rate|
    reliability_curve: tuple[CalibrationBucket, ...] = ()
    created_ts: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())


# ---------------------------------------------------------------------------
# Adaptive routing evidence (§35-§37)


class ArmKind(StrEnum):
    MODEL = "MODEL"
    STRATEGY = "STRATEGY"


class RoutingStats(BaseModel):
    """Accumulated per-arm, per-task-class evidence for adaptive routing
    (§35). Incremental aggregates — means are derived, never stored stale."""

    model_config = ConfigDict(frozen=True)

    arm_kind: ArmKind
    arm: str
    task_class: str
    runs: int = 0
    successes: int = 0
    quality_sum: float = 0.0
    latency_sum: float = 0.0
    cost_sum: float = 0.0
    exploration_runs: int = 0  # §36/§37: measurable exploration
    updated_ts: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())

    @property
    def success_rate(self) -> float:
        return self.successes / self.runs if self.runs else 0.0

    @property
    def quality_avg(self) -> float:
        return self.quality_sum / self.runs if self.runs else 0.0


class RoutingChoice(BaseModel):
    """One routing decision: which arm, and whether it was exploration."""

    model_config = ConfigDict(frozen=True)

    arm: str
    explored: bool = False
    reason: str = ""


# ---------------------------------------------------------------------------
# Generalization (§38-§39)


class GeneralizationReport(BaseModel):
    """§38: in_domain / unseen / transfer / robustness scores plus the §39
    gate verdict. A benchmark-only improvement with collapsing unseen
    performance FAILS the gate."""

    model_config = ConfigDict(frozen=True)

    experiment_id: str
    in_domain: float
    unseen: float
    transfer: float | None = None
    robustness: float | None = None
    gate_passed: bool = False
    reasons: tuple[str, ...] = ()
    created_ts: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())


# ---------------------------------------------------------------------------
# Adversarial evaluation (§40)


class PerturbationKind(StrEnum):
    """The §40 perturbation catalogue a robust strategy must survive."""

    AMBIGUITY = "AMBIGUITY"
    WRONG_ASSUMPTIONS = "WRONG_ASSUMPTIONS"
    MISSING_DATA = "MISSING_DATA"
    CONTRADICTORY_EVIDENCE = "CONTRADICTORY_EVIDENCE"
    PROMPT_INJECTION = "PROMPT_INJECTION"
    MALICIOUS_DOCUMENTS = "MALICIOUS_DOCUMENTS"
    TOOL_FAILURE = "TOOL_FAILURE"
    PROVIDER_FAILURE = "PROVIDER_FAILURE"
    STALE_INFORMATION = "STALE_INFORMATION"
    UNEXPECTED_UI_STATE = "UNEXPECTED_UI_STATE"


class AdversarialResult(BaseModel):
    """Survival of one strategy under one perturbation class (§40)."""

    model_config = ConfigDict(frozen=True)

    strategy_id: str
    perturbation: PerturbationKind
    n_tasks: int = 0
    survived: int = 0
    survival_rate: float = 0.0
    created_ts: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())


# ---------------------------------------------------------------------------
# Recovery & long-horizon evaluation (§41-§42)


class RecoveryEvaluation(BaseModel):
    """§41: initial failure, recovery success, retries, extra cost and
    quality after recovery. Intelligent recovery can beat fragile success."""

    model_config = ConfigDict(frozen=True)

    trajectory_id: str
    initial_failure: bool = False
    recovered: bool = False
    recovery_steps: int = 0
    additional_cost_usd: float = 0.0
    quality_after_recovery: float | None = None
    score: float = 0.0
    created_ts: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())


class LongHorizonResult(BaseModel):
    """§42: goal completion, error accumulation, plan drift, verification
    quality and recovery over long multi-step tasks."""

    model_config = ConfigDict(frozen=True)

    trajectory_id: str
    steps: int = 0
    goal_completion: float = 0.0
    error_accumulation: float = 0.0
    plan_drift: float = 0.0
    verification_quality: float | None = None
    recovery: float | None = None
    score: float = 0.0
    created_ts: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())


# ---------------------------------------------------------------------------
# Evaluation dataset & synthetic variants (§43-§44)


class EvalSample(BaseModel):
    """§43: one evaluation sample with full metadata."""

    model_config = ConfigDict(frozen=True)

    sample_id: str = Field(default_factory=lambda: f"es_{uuid.uuid4().hex[:12]}")
    task: str
    domain: str = ""
    difficulty: Literal["easy", "medium", "hard"] = "medium"
    success_criteria: str = ""
    allowed_capabilities: tuple[str, ...] = ()
    risk: Literal["low", "medium", "high"] = "low"
    evaluation_method: str = "automated"
    source: Literal["golden", "failure", "research", "synthetic"] = "golden"
    approved: bool = True  # golden/failure-derived samples ship approved
    created_ts: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())


class VariantKind(StrEnum):
    """§44: how a synthetic variant differs from its source task."""

    PARAPHRASE = "PARAPHRASE"
    DIFFERENT_FILES = "DIFFERENT_FILES"
    DIFFERENT_WEBSITES = "DIFFERENT_WEBSITES"
    DIFFERENT_DATA = "DIFFERENT_DATA"
    DIFFERENT_CONSTRAINTS = "DIFFERENT_CONSTRAINTS"
    DIFFERENT_TOOL_AVAILABILITY = "DIFFERENT_TOOL_AVAILABILITY"


class SyntheticVariant(BaseModel):
    """§44: generated task variant. Starts as DRAFT — only human review
    promotes it to GOLDEN."""

    model_config = ConfigDict(frozen=True)

    variant_id: str = Field(default_factory=lambda: f"sv_{uuid.uuid4().hex[:12]}")
    source_sample_id: str
    kind: VariantKind
    task: str
    status: Literal["DRAFT", "APPROVED", "REJECTED", "GOLDEN"] = "DRAFT"
    created_ts: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())


# ---------------------------------------------------------------------------
# Learning budget, adaptation curve, regression protection (§45-§53)


class AdaptationBudget(BaseModel):
    """§47: hard caps on what one learning cycle may consume. Normal user
    tasks always come first — when a cap is hit, learning pauses, it never
    slows down the user."""

    model_config = ConfigDict(frozen=True)

    cpu_seconds: float = 300.0
    memory_mb: float = 512.0
    model_calls: int = 50
    tokens: int = 200_000
    time_minutes: float = 30.0
    disk_mb: float = 100.0
    network_mb: float = 20.0


class BudgetUsage(BaseModel):
    """Cumulative resource consumption of one learning cycle (§47)."""

    model_config = ConfigDict(frozen=True)

    cpu_seconds: float = 0.0
    memory_mb: float = 0.0
    model_calls: int = 0
    tokens: int = 0
    time_minutes: float = 0.0
    disk_mb: float = 0.0
    network_mb: float = 0.0

    def exceeded_limits(self, budget: AdaptationBudget) -> tuple[str, ...]:
        limits: list[str] = []
        if self.cpu_seconds > budget.cpu_seconds:
            limits.append("cpu_seconds")
        if self.memory_mb > budget.memory_mb:
            limits.append("memory_mb")
        if self.model_calls > budget.model_calls:
            limits.append("model_calls")
        if self.tokens > budget.tokens:
            limits.append("tokens")
        if self.time_minutes > budget.time_minutes:
            limits.append("time_minutes")
        if self.disk_mb > budget.disk_mb:
            limits.append("disk_mb")
        if self.network_mb > budget.network_mb:
            limits.append("network_mb")
        return tuple(limits)


class CycleSnapshot(BaseModel):
    """§50: one point on the adaptation curve — measured performance across
    the 7 metric families at the end of a learning cycle."""

    model_config = ConfigDict(frozen=True)

    cycle_id: str
    success_rate: float | None = None
    error_rate: float | None = None
    latency_ms: float | None = None
    cost_usd: float | None = None
    step_count: float | None = None
    recovery_success_rate: float | None = None
    verification_rate: float | None = None
    tokens_per_task: float | None = None
    created_ts: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())


class LearningEfficiency(BaseModel):
    """§51: ATLAS must become better EFFICIENTLY — gain per unit of learning
    resource. None means the ratio is undefined (zero denominator), never
    fabricated."""

    model_config = ConfigDict(frozen=True)

    cycle_id: str
    performance_gain: float = 0.0
    per_experience: float | None = None
    per_model_call: float | None = None
    per_token_cost: float | None = None
    per_learning_time: float | None = None
    created_ts: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())


class SuiteResult(BaseModel):
    """§52: one regression suite run for one promotion candidate. Suites:
    baseline, safety, generalization, performance, critical."""

    model_config = ConfigDict(frozen=True)

    suite: Literal["baseline", "safety", "generalization", "performance", "critical"]
    domain: str = ""
    passed: bool
    score: float | None = None
    detail: str = ""


class RegressionReport(BaseModel):
    """§52-§53: verdict over all suites for a candidate. If a domain regresses
    while others improve the recommendation is a domain-scoped strategy, not
    a global promotion."""

    model_config = ConfigDict(frozen=True)

    experiment_id: str
    all_passed: bool
    blocking_suites: tuple[str, ...] = ()
    regressed_domains: tuple[str, ...] = ()
    recommendation: Literal["promote", "domain_scope", "reject"] = "reject"
    created_ts: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())


# ---------------------------------------------------------------------------
# Domain feedback loops (§54-§69)


class ToolPerformanceRecord(BaseModel):
    """§57: one observed tool execution. Aggregates answer routing questions
    like 'for repository code search, tool A is more reliable than B'."""

    model_config = ConfigDict(frozen=True)

    tool_name: str
    task_class: str = ""
    success: bool
    latency_ms: float = 0.0
    failure_reason: str | None = None
    recovered: bool = False
    created_ts: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())


class SourceTrustRecord(BaseModel):
    """§58: conservative trust state for one knowledge source. Frequency is
    never equated with truth — trust moves slowly and contradictions
    matter."""

    model_config = ConfigDict(frozen=True)

    source: str
    usefulness: float = 0.5
    claim_correctness: float = 0.5
    citation_acceptance: float = 0.5
    freshness_score: float = 0.5
    contradiction_rate: float = 0.0
    trust: float = 0.5
    n_observations: int = 0
    updated_ts: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())


class MemoryFeedbackRecord(BaseModel):
    """§59: was a retrieved memory useful for this task?"""

    model_config = ConfigDict(frozen=True)

    memory_id: str
    task_id: str
    helped: bool = False
    distracted: bool = False
    stale: bool = False
    rating: float = 0.0
    created_ts: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())


class FeedbackKind(StrEnum):
    """§61: the kinds of human feedback ATLAS accepts."""

    THUMBS_UP = "thumbs_up"
    THUMBS_DOWN = "thumbs_down"
    CORRECTION = "correction"
    EXPECTED_ANSWER = "expected_answer"
    PREFERRED_STRATEGY = "preferred_strategy"
    REASON = "reason"


class HumanFeedbackRecord(BaseModel):
    """§61: feedback connected to a trajectory/decision/tool/strategy/
    knowledge/model. Not all feedback is equally reliable — the weight is
    explicit."""

    model_config = ConfigDict(frozen=True)

    feedback_id: str = Field(default_factory=lambda: f"hf_{uuid.uuid4().hex[:12]}")
    kind: FeedbackKind
    ref_kind: Literal["trajectory", "decision", "tool", "strategy", "knowledge", "model"]
    ref_id: str = ""
    content: str = ""
    reliability: float = 0.5
    created_ts: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())


class UserCorrection(BaseModel):
    """§62: a recorded user correction, e.g. 'use official docs instead'.
    One correction increases preference — it never becomes universal
    policy."""

    model_config = ConfigDict(frozen=True)

    task_class: str
    preferred_source_strategy: str
    context: str = ""
    count: int = 1
    created_ts: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())


class ResearchSessionFeedback(BaseModel):
    """§63-§64: what a research run spent and produced — the evidence base
    for learning when to stop browsing."""

    model_config = ConfigDict(frozen=True)

    task_id: str
    sources_searched: int
    unique_information: float
    answer_quality: float
    created_ts: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())


class VerificationLevel(StrEnum):
    """§66: learned verification intensity per task class."""

    NONE = "none"
    LIGHT = "light"
    DEEP = "deep"
    MULTI_SOURCE = "multi_source"
    HUMAN = "human"


class VerificationPreference(BaseModel):
    """§66: verification is learnable EXCEPT where policy locks it —
    safety-critical verification stays policy-controlled."""

    model_config = ConfigDict(frozen=True)

    task_class: str
    level: VerificationLevel
    evidence_count: int = 0
    policy_locked: bool = False
    updated_ts: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())


class CapabilityStat(BaseModel):
    """§69: historical performance attached to capability metadata — the
    evidence-driven routing input."""

    model_config = ConfigDict(frozen=True)

    capability: str
    success_rate: float = 0.0
    avg_latency_ms: float = 0.0
    n_samples: int = 0
    updated_ts: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())


__all__ = [
    "MIN_EVIDENCE_DEFAULT",
    "AdaptationBudget",
    "AdaptationPoint",
    "AdversarialResult",
    "AllowedChangeType",
    "ArmKind",
    "BudgetUsage",
    "CalibrationBucket",
    "CalibrationReport",
    "CanaryDeployment",
    "CanaryObservation",
    "CanaryStatus",
    "CognitiveTelemetry",
    "CapabilityStat",
    "ComparisonResult",
    "CounterfactualMode",
    "CounterfactualResult",
    "CycleSnapshot",
    "DecisionPreference",
    "DecisionQuality",
    "EvalSample",
    "EvaluationVerdict",
    "Experiment",
    "ExperimentArm",
    "ExperimentStatus",
    "FailureAnalysis",
    "FeedbackKind",
    "ForbiddenChangeType",
    "GeneralizationReport",
    "GeneralizationResult",
    "Hypothesis",
    "HypothesisStatus",
    "HumanFeedbackRecord",
    "LearningEfficiency",
    "LearningState",
    "LongHorizonResult",
    "MemoryFeedbackRecord",
    "OutcomeEvaluation",
    "PerturbationKind",
    "PromotionDecision",
    "PromotionState",
    "RecoveryEvaluation",
    "RegressionReport",
    "ResearchSessionFeedback",
    "ResourceLimits",
    "RoutingChoice",
    "RoutingStats",
    "ShadowComparison",
    "ShadowDecision",
    "ShadowVerdict",
    "StrategyPerformance",
    "StrategyVersion",
    "SourceTrustRecord",
    "SuiteResult",
    "SyntheticVariant",
    "ToolPerformanceRecord",
    "TrajectoryEvaluation",
    "UserCorrection",
    "VariantKind",
    "VerificationLevel",
    "VerificationPreference",
]
