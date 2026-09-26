"""Routing IR — the typed heart of the routing fabric (Part 3 §5).

These models are the contract between every routing layer. They are frozen
pydantic models (ATLAS convention) so decisions are replayable (§66) and
persistable (§55): a RouteDecision stored as JSON reconstructs exactly.

Confidence semantics are deliberately SEPARATE (§63): deterministic certainty,
judgment confidence (Jev or LLM), and overall route confidence are different
fields with different meanings — never merged into one uncalibrated number.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from atlas.infra.types import CostPolicy, NetworkPolicy, PrivacyClass


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True)


# ── Task IR (§6) ───────────────────────────────────────────────────────── #


class Complexity(StrEnum):
    SIMPLE = "simple"
    MODERATE = "moderate"
    COMPLEX = "complex"


class Urgency(StrEnum):
    NORMAL = "normal"
    HIGH = "high"


class TaskIR(_Frozen):
    """Normalized, structured description of one inbound request (§6).
    Only fields the router actually consumes — no speculative payload."""

    task_id: str
    correlation_id: str
    objective: str
    source: str = "api"  # api | cli | scheduler | voice | system (§12: source preserved)
    domain_candidates: tuple[str, ...] = ()
    intent_candidates: tuple[str, ...] = ()
    required_capabilities: tuple[str, ...] = ()
    constraints: tuple[str, ...] = ()
    preferences: tuple[str, ...] = ()
    complexity: Complexity = Complexity.MODERATE
    urgency: Urgency = Urgency.NORMAL
    risk: int = 0  # 0..4, mirrors infra Tier vocabulary
    privacy_class: PrivacyClass = PrivacyClass.PUBLIC
    network_policy: NetworkPolicy = NetworkPolicy.FREE_CLOUD
    cost_policy: CostPolicy = CostPolicy.FREE_ONLY
    max_risk: int = 4


# ── Candidates (§17) ───────────────────────────────────────────────────── #


class CandidateType(StrEnum):
    TOOL = "tool"
    AGENT = "agent"
    WORKFLOW = "workflow"
    MODEL = "model"
    PROVIDER = "provider"
    DOMAIN = "domain"
    STRATEGY = "strategy"


class RouteCandidate(_Frozen):
    """One routable option. Part 3 populates TOOL (from the Part-2 catalog),
    DOMAIN, and STRATEGY candidates; the IR supports AGENT/WORKFLOW/MODEL/
    PROVIDER so backing systems can register later without a redesign (§17)."""

    candidate_type: CandidateType
    candidate_id: str  # e.g. tool id, domain id, strategy id
    display_name: str = ""
    description: str = ""
    capability: str | None = None
    operations: tuple[str, ...] = ()
    # Policy-relevant descriptors (mirror the catalog record; §20 filtering).
    status: str = "READY"
    enabled: bool = True
    availability: str = "AVAILABLE"
    requires_auth: bool = False
    auth_state: str = "NONE"
    cost_class: str = "free"
    estimated_cost_usd: float = 0.0
    estimated_latency_ms: int = 500
    locality: str = "local"
    privacy_class: str = "public"  # max sensitivity this candidate may process
    network_required: bool = False
    trust_level: str = "system_builtin"
    side_effects: bool = False
    idempotent: bool = True
    safety_tool: str = ""
    tags: tuple[str, ...] = ()
    metadata: dict[str, Any] = Field(default_factory=dict)
    # Discovery provenance — WHY this candidate was even considered (§68).
    origin: str = "catalog"  # catalog | domain_registry | strategy_registry | static


# ── Judgment (§22/§29/§30/§63) ─────────────────────────────────────────── #


class ConfidenceLevel(StrEnum):
    HIGH = "HIGH_CONFIDENCE"
    MEDIUM = "MEDIUM_CONFIDENCE"
    LOW = "LOW_CONFIDENCE"


class DecisionRisk(StrEnum):
    """Risk class of the DECISION ITSELF (not the task) — drives thresholds (§29)."""

    LOW_RISK_ROUTING = "low_risk_routing"
    MEDIUM_RISK = "medium_risk"
    HIGH_RISK = "high_risk"
    DESTRUCTIVE = "destructive"


class JudgmentResult(_Frozen):
    """Normalized answer to ONE atomic judgment question (§25)."""

    question_id: str
    provider: str  # deterministic | jev | llm
    choice: str | None = None
    score: float | None = None  # provider's raw score/confidence, 0..1 when given
    confidence: ConfidenceLevel = ConfidenceLevel.LOW
    raw_probabilities: dict[str, float] = Field(default_factory=dict)  # retained for telemetry (§30)
    accepted: bool = False  # passed the threshold policy?
    reason: str = ""
    latency_ms: int = 0
    fallback_used: bool = False  # provider fell back down the ladder (§32)


class JudgmentBatch(_Frozen):
    """All judgment results for one routing decision + which policy applied."""

    results: tuple[JudgmentResult, ...] = ()
    threshold_policy: str = ""
    provider_chain: tuple[str, ...] = ()  # e.g. ("deterministic", "jev", "llm")


# ── Decision (§35) ─────────────────────────────────────────────────────── #


class RejectedCandidate(_Frozen):
    candidate_id: str
    reason: str
    stage: str  # "hard_filter" | "ranking" | "judgment" | "validation"


class RouteDecision(_Frozen):
    """The complete, persistable outcome of one routing pass (§35)."""

    route_id: str
    request_id: str  # == TaskIR.task_id
    correlation_id: str
    objective: str
    decision_type: str  # "route" | "ask_user" | "request_confirmation" | "human_review" | "no_route" (§64)
    domain: str = ""
    strategy: str = ""
    candidates: tuple[RouteCandidate, ...] = ()
    filtered_candidates: tuple[str, ...] = ()
    rejected: tuple[RejectedCandidate, ...] = ()
    judgment: JudgmentBatch = Field(default_factory=JudgmentBatch)
    ranking_scores: dict[str, float] = Field(default_factory=dict)
    score_components: dict[str, dict[str, float]] = Field(default_factory=dict)  # §34: per-candidate breakdown
    selected_candidate: str | None = None
    fallback_candidates: tuple[str, ...] = ()
    # Separated confidence semantics (§63).
    deterministic_certainty: float = 0.0  # 1.0 when a rule decided with no ambiguity
    judgment_confidence: float | None = None  # provider confidence for THIS decision
    judgment_provider: str | None = None
    overall_confidence: float = 0.0
    confidence_level: ConfidenceLevel = ConfidenceLevel.LOW
    reason: str = ""
    reasons: tuple[str, ...] = Field(default_factory=tuple)  # structured per-layer explanations (§68)
    policy_snapshot: dict[str, Any] = Field(default_factory=dict)
    catalog_version: int = 0
    created_at: datetime | None = None
    duration_ms: int = 0


# ── Plan / Step / Graph (§36-§38) ──────────────────────────────────────── #


class RouteStep(_Frozen):
    step_id: str
    candidate_id: str | None = None  # tool/agent/workflow id (None for control nodes)
    candidate_type: CandidateType = CandidateType.TOOL
    action: str = "execute"  # execute | decide | verify | replan | human_review | end
    domain: str | None = None  # set on cross-domain steps (§73)
    operation: str | None = None
    input_mapping: dict[str, Any] = Field(default_factory=dict)
    depends_on: tuple[str, ...] = ()  # step_ids; empty = root
    timeout_s: float | None = None
    max_retries: int = 0
    success_condition: str = "ok"  # ok | output_matches:<key> | custom predicate id
    failure_condition: str = "error"
    verification: str | None = None  # verification strategy id, when required
    execution_mode: str = "serial"  # serial | parallel (with sibling parallel steps)


class RoutePlan(_Frozen):
    plan_id: str
    task_id: str
    route_id: str
    strategy: str
    domain: str
    steps: tuple[RouteStep, ...] = ()
    fallbacks: tuple[str, ...] = ()  # candidate ids, primary first
    budgets: dict[str, float] = Field(default_factory=dict)  # sourced from ExecutionLimits (§54)
    termination_conditions: tuple[str, ...] = ()
    verification_conditions: tuple[str, ...] = ()
    template: str | None = None  # static template id, when one was used (§39)


class GraphNodeType(StrEnum):
    EXECUTE = "EXECUTE"
    DECIDE = "DECIDE"
    PARALLEL = "PARALLEL"
    JOIN = "JOIN"
    VERIFY = "VERIFY"
    REPLAN = "REPLAN"
    HUMAN_REVIEW = "HUMAN_REVIEW"
    END = "END"


class GraphEdgeCondition(StrEnum):
    SUCCESS = "success"
    FAILURE = "failure"
    TIMEOUT = "timeout"
    LOW_CONFIDENCE = "low_confidence"
    VERIFICATION_FAILED = "verification_failed"
    NEEDS_MORE_INFORMATION = "needs_more_information"


class RouteNode(_Frozen):
    node_id: str
    node_type: GraphNodeType
    step_id: str | None = None
    label: str = ""


class RouteEdge(_Frozen):
    from_node: str
    to_node: str
    condition: GraphEdgeCondition = GraphEdgeCondition.SUCCESS


class RouteGraph(_Frozen):
    """Explicit execution graph (§38) — the shape research/IDE workflows will
    compile to later. Also the fallback representation: recovery nodes hang
    off failure edges (§53)."""

    plan_id: str
    nodes: tuple[RouteNode, ...] = ()
    edges: tuple[RouteEdge, ...] = ()

    def successors(self, node_id: str, condition: GraphEdgeCondition | None = None) -> list[str]:
        out = []
        for edge in self.edges:
            if edge.from_node == node_id and (condition is None or edge.condition == condition):
                out.append(edge.to_node)
        return out

    def entry_nodes(self) -> list[str]:
        targets = {e.to_node for e in self.edges}
        return [n.node_id for n in self.nodes if n.node_id not in targets] or [n.node_id for n in self.nodes]

    def has_terminal(self) -> bool:
        """§84: every route must have a valid terminal outcome."""
        return any(n.node_type == GraphNodeType.END for n in self.nodes)


# ── Recovery (§51/§52) ─────────────────────────────────────────────────── #


class FailureCategory(StrEnum):
    AUTH = "AUTH"
    UNAVAILABLE = "UNAVAILABLE"
    TIMEOUT = "TIMEOUT"
    RATE_LIMIT = "RATE_LIMIT"
    QUOTA = "QUOTA"
    BAD_ARGUMENT = "BAD_ARGUMENT"
    SCHEMA = "SCHEMA"
    BAD_RESULT = "BAD_RESULT"
    ENVIRONMENT = "ENVIRONMENT"
    AGENT_STALL = "AGENT_STALL"
    GOAL_MISMATCH = "GOAL_MISMATCH"
    VERIFICATION_FAILED = "VERIFICATION_FAILED"
    UNKNOWN = "UNKNOWN"


class RecoveryAction(StrEnum):
    RETRY = "retry"
    FALLBACK = "fallback"  # Part 4 §63: switch to the next fallback candidate
    ALTERNATE_CANDIDATE = "alternate_candidate"
    ALTERNATE_PROVIDER = "alternate_provider"
    RETRY_WITH_CHANGED_PARAMETERS = "retry_with_changed_parameters"
    VALIDATE_RESULT = "validate_result"
    REPLAN = "replan"
    ESCALATE_HUMAN = "escalate_human"
    TERMINATE = "terminate"


class RecoveryDecision(_Frozen):
    """One step down the recovery graph (§52/§53)."""

    failure_category: FailureCategory
    action: RecoveryAction
    candidate_id: str | None = None  # target of alternate_candidate, when applicable
    attempt: int = 1
    max_attempts: int = 3
    reason: str = ""
    exhausted: bool = False  # recovery bounds exceeded → terminate (§50/§84)


# ── Progress (§48-§50) ─────────────────────────────────────────────────── #


class ProgressState(_Frozen):
    """Snapshot of a ProgressLedger at one moment."""

    objective: str = ""
    subgoals: tuple[str, ...] = ()
    completed: tuple[str, ...] = ()
    in_progress: tuple[str, ...] = ()
    blocked: tuple[str, ...] = ()
    failed: tuple[str, ...] = ()
    attempt_count: int = 0
    last_progress_ts: datetime | None = None


__all__ = [
    "CandidateType",
    "Complexity",
    "ConfidenceLevel",
    "DecisionRisk",
    "FailureCategory",
    "GraphEdgeCondition",
    "GraphNodeType",
    "JudgmentBatch",
    "JudgmentResult",
    "ProgressState",
    "RecoveryAction",
    "RecoveryDecision",
    "RejectedCandidate",
    "RouteCandidate",
    "RouteDecision",
    "RouteEdge",
    "RouteGraph",
    "RouteNode",
    "RoutePlan",
    "RouteStep",
    "TaskIR",
    "Urgency",
]
