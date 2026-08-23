"""Typed engineering-intelligence objects (Prompt 5 §3-§5, §27-§28, §52, §81).

WHY a new package rather than more `adaptation`: the adaptation plane is
trajectory-scoped — it learns from tasks that went wrong. Nothing in it models
the *system* going wrong: a worker crash-looping, an event queue backing up, a
schema mismatch, a provider outage. Those have no trajectory, so they have no
home in `adaptation.taxonomy`.

WHY these are pydantic models and not JSON blobs: identical reasoning to
`adaptation.domain` — an incident is read by the CLI, the API, the frontend and
the repair pipeline, and a dict would let each of them disagree about the shape.

WHAT THIS DELIBERATELY REUSES (Prompt 5 §1 forbids parallel systems):

- `adaptation.taxonomy.FailureClass` classifies *what kind* of failure. This
  module adds `Severity` — *how urgent* — which the taxonomy has never modelled.
- `adaptation.domain.ForbiddenChangeType` already enumerates what self-change
  may never touch. `RepairType` is checked against it rather than re-listing it.
- `infra.errors.AtlasError.code` is the fingerprint input (see `fingerprint.py`).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from enum import IntEnum, StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from atlas.adaptation.taxonomy import FailureClass


def _now() -> str:
    return datetime.now(UTC).isoformat()


# ---------------------------------------------------------------------------
# Severity (§4) — severity controls autonomy


class Severity(StrEnum):
    """How urgent, independent of what kind.

    §4: "Severity must control autonomy." The ordering matters, so compare via
    `severity_rank()` rather than string comparison — StrEnum compares
    lexically, which would make CRITICAL < HIGH < INFO < LOW < MEDIUM.
    """

    INFO = "INFO"
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


_SEVERITY_ORDER: dict[Severity, int] = {
    Severity.INFO: 0,
    Severity.LOW: 1,
    Severity.MEDIUM: 2,
    Severity.HIGH: 3,
    Severity.CRITICAL: 4,
}


def severity_rank(severity: Severity) -> int:
    """Total order over severities. Higher is worse."""
    return _SEVERITY_ORDER[severity]


def max_severity(severities: tuple[Severity, ...]) -> Severity:
    """Worst of a set; INFO for an empty set (nothing observed is not an alarm)."""
    if not severities:
        return Severity.INFO
    return max(severities, key=severity_rank)


# ---------------------------------------------------------------------------
# Lifecycle (§3) — twelve statuses, DETECTED → CLOSED


class IncidentStatus(StrEnum):
    """The incident lifecycle.

    Terminal states are RESOLVED, CLOSED and REPAIR_REJECTED. HUMAN_REVIEW_REQUIRED
    is NOT terminal — it is a park, and it is the state every ineligible repair
    lands in rather than being silently dropped (§33).
    """

    DETECTED = "DETECTED"
    TRIAGED = "TRIAGED"
    CORRELATED = "CORRELATED"
    DIAGNOSING = "DIAGNOSING"
    DIAGNOSED = "DIAGNOSED"
    REPAIR_PROPOSED = "REPAIR_PROPOSED"
    REPAIR_TESTING = "REPAIR_TESTING"
    REPAIR_VERIFIED = "REPAIR_VERIFIED"
    REPAIR_REJECTED = "REPAIR_REJECTED"
    HUMAN_REVIEW_REQUIRED = "HUMAN_REVIEW_REQUIRED"
    RESOLVED = "RESOLVED"
    CLOSED = "CLOSED"


TERMINAL_STATUSES: frozenset[IncidentStatus] = frozenset(
    {IncidentStatus.RESOLVED, IncidentStatus.CLOSED, IncidentStatus.REPAIR_REJECTED}
)

ACTIVE_STATUSES: frozenset[IncidentStatus] = frozenset(set(IncidentStatus) - TERMINAL_STATUSES)


# ---------------------------------------------------------------------------
# Sources (§5) — every signal enters through ONE model


class IncidentSource(StrEnum):
    """Where the signal came from.

    §5: ~20 sources, one `Incident` model. The source is recorded so a detector
    can be audited ("which detector produced this?") and so a whole class can be
    muted without muting the model.
    """

    RUNTIME_EXCEPTION = "RUNTIME_EXCEPTION"
    HEALTH_CHECK = "HEALTH_CHECK"
    WORKER_CRASH = "WORKER_CRASH"
    PROVIDER_FAILURE = "PROVIDER_FAILURE"
    MODEL_REGRESSION = "MODEL_REGRESSION"
    EVENT_PIPELINE = "EVENT_PIPELINE"
    TASK_FAILURE = "TASK_FAILURE"
    TASK_STALL = "TASK_STALL"
    DEADLOCK = "DEADLOCK"
    RESOURCE_LEAK = "RESOURCE_LEAK"
    DB_INTEGRITY = "DB_INTEGRITY"
    MEMORY_INTEGRITY = "MEMORY_INTEGRITY"
    RAG_REGRESSION = "RAG_REGRESSION"
    EVALUATION_REGRESSION = "EVALUATION_REGRESSION"
    PERFORMANCE_REGRESSION = "PERFORMANCE_REGRESSION"
    SAFETY_EVENT = "SAFETY_EVENT"
    SECURITY_SCAN = "SECURITY_SCAN"
    API_CONTRACT = "API_CONTRACT"
    FRONTEND_ERROR = "FRONTEND_ERROR"
    COMPUTER_USE = "COMPUTER_USE"
    ANOMALY_DETECTOR = "ANOMALY_DETECTOR"
    SCHEDULED_CHECK = "SCHEDULED_CHECK"
    USER_REPORT = "USER_REPORT"


# ---------------------------------------------------------------------------
# Measurement provenance (§9)


class Provenance(StrEnum):
    """§9: quality telemetry must clearly distinguish how a number was obtained.

    MEASURED   — read from the system or computed deterministically from it.
    ESTIMATED  — derived from a model or a price table; right order of magnitude.
    HEURISTIC  — a rule of thumb (e.g. characters ÷ 4 for tokens).

    A number with no provenance is not publishable in the UI. This exists so a
    chart can never present a heuristic as a measurement.
    """

    MEASURED = "MEASURED"
    ESTIMATED = "ESTIMATED"
    HEURISTIC = "HEURISTIC"


class Measurement(BaseModel):
    """One number with its provenance and unit attached."""

    model_config = ConfigDict(frozen=True)

    name: str
    value: float
    unit: str = ""
    provenance: Provenance = Provenance.MEASURED
    n_samples: int = 0
    note: str = ""


# ---------------------------------------------------------------------------
# Correlation (§7)


class CorrelationKeys(BaseModel):
    """The seven ids §7 names. All optional — most signals carry a subset.

    These already exist individually across the runtime (`request_id` at the HTTP
    edge, `correlation_id` on `Event`, the rest as columns). What did not exist is
    a single record that holds them together, which is the whole point of §7.
    """

    model_config = ConfigDict(frozen=True)

    request_id: str | None = None
    correlation_id: str | None = None
    task_id: str | None = None
    trajectory_id: str | None = None
    step_id: str | None = None
    tool_call_id: str | None = None
    workflow_run_id: str | None = None

    def known(self) -> dict[str, str]:
        """Only the ids actually present — for logging and for SQL filters."""
        return {
            name: value
            for name in (
                "request_id",
                "correlation_id",
                "task_id",
                "trajectory_id",
                "step_id",
                "tool_call_id",
                "workflow_run_id",
            )
            if (value := getattr(self, name)) is not None
        }

    def is_empty(self) -> bool:
        return not self.known()


# ---------------------------------------------------------------------------
# Evidence (§58) — concrete or it does not count


class EvidenceKind(StrEnum):
    """What sort of artifact backs a claim.

    §58 requires concrete evidence. Every kind here points at something durable
    that can be re-read later; there is deliberately no "MODEL_OPINION" kind.
    """

    LOG_LINE = "LOG_LINE"
    EVENT_ROW = "EVENT_ROW"
    DB_ROW = "DB_ROW"
    METRIC_WINDOW = "METRIC_WINDOW"
    HEALTH_REPORT = "HEALTH_REPORT"
    TRAJECTORY_STEP = "TRAJECTORY_STEP"
    LLM_CALL = "LLM_CALL"
    EVALUATION_RESULT = "EVALUATION_RESULT"
    AUDIT_RECORD = "AUDIT_RECORD"
    STACK_FRAME = "STACK_FRAME"
    SOURCE_LOCATION = "SOURCE_LOCATION"
    TEST_RESULT = "TEST_RESULT"


class Evidence(BaseModel):
    """One concrete artifact supporting or contradicting a claim.

    `summary` is redacted before storage (see `store.py`). `ref` identifies the
    durable row/file the summary came from, so a reviewer can go and look rather
    than trusting the summary.
    """

    model_config = ConfigDict(frozen=True)

    kind: EvidenceKind
    ref: str
    summary: str = ""
    observed_ts: str = Field(default_factory=_now)
    supports: bool = True  # False == this artifact CONTRADICTS the claim (§57)


# ---------------------------------------------------------------------------
# Incident (§3)


class Incident(BaseModel):
    """One system-scoped problem.

    Deduplicated by `fingerprint` — a second occurrence increments
    `occurrence_count` and moves `last_seen_ts` rather than creating a new row
    (§15). §13's rule ("20 errors from the same event are not 20 incidents") is
    therefore enforced by the store, not by convention.
    """

    model_config = ConfigDict(frozen=True)

    incident_id: str = Field(default_factory=lambda: f"inc_{uuid.uuid4().hex[:12]}")
    fingerprint: str
    title: str
    summary: str = ""
    source: IncidentSource
    severity: Severity = Severity.LOW
    status: IncidentStatus = IncidentStatus.DETECTED
    component: str = ""
    failure_class: FailureClass | None = None
    detector: str = ""
    correlation: CorrelationKeys = Field(default_factory=CorrelationKeys)
    parent_incident_id: str | None = None
    related_incident_ids: tuple[str, ...] = ()
    occurrence_count: int = 1
    first_seen_ts: str = Field(default_factory=_now)
    last_seen_ts: str = Field(default_factory=_now)
    updated_ts: str = Field(default_factory=_now)
    resolved_ts: str | None = None
    evidence: tuple[Evidence, ...] = ()
    measurements: tuple[Measurement, ...] = ()
    diagnosis_id: str | None = None
    repair_id: str | None = None
    repair_attempts: int = 0
    escalated: bool = False
    escalation_reason: str = ""
    notes: tuple[str, ...] = ()

    @property
    def is_active(self) -> bool:
        return self.status in ACTIVE_STATUSES

    @property
    def needs_human(self) -> bool:
        return self.status is IncidentStatus.HUMAN_REVIEW_REQUIRED or self.escalated

    def contradicting_evidence(self) -> tuple[Evidence, ...]:
        return tuple(e for e in self.evidence if not e.supports)


# ---------------------------------------------------------------------------
# Security incidents (§52) — a SEPARATE model, on purpose


class SecurityIncidentKind(StrEnum):
    """§52: the security event classes ATLAS can recognise today.

    Only kinds with a real detector behind them belong here. Adding a kind with
    no detector would make `/system/security` claim coverage it does not have.
    """

    PROMPT_INJECTION = "PROMPT_INJECTION"
    MALICIOUS_DOCUMENT = "MALICIOUS_DOCUMENT"
    SAFETY_POLICY_VIOLATION = "SAFETY_POLICY_VIOLATION"
    UNAUTHORIZED_ACCESS = "UNAUTHORIZED_ACCESS"
    CREDENTIAL_EXPOSURE_RISK = "CREDENTIAL_EXPOSURE_RISK"
    SANDBOX_ESCAPE_ATTEMPT = "SANDBOX_ESCAPE_ATTEMPT"
    AUDIT_CHAIN_BREAK = "AUDIT_CHAIN_BREAK"
    UNEXPECTED_TOOL_EXECUTION = "UNEXPECTED_TOOL_EXECUTION"


class ContainmentAction(StrEnum):
    """§53: what was actually done, not what was recommended.

    NONE is a real answer and must stay available — recording NONE is how a gap
    becomes visible. A detector that always claims containment it did not perform
    is worse than one that admits it only observed.
    """

    NONE = "NONE"
    BLOCKED = "BLOCKED"
    QUARANTINED = "QUARANTINED"
    TOOL_DISABLED = "TOOL_DISABLED"
    KILL_SWITCH = "KILL_SWITCH"
    HUMAN_NOTIFIED = "HUMAN_NOTIFIED"


class SecurityIncident(BaseModel):
    """§52: kept separate from `Incident` because the response chain differs.

    An operational incident asks "what broke and can we fix it?". A security
    incident asks "what was attempted, was it contained, and is the evidence
    preserved?" — and it is NEVER auto-repaired (§83).
    """

    model_config = ConfigDict(frozen=True)

    security_incident_id: str = Field(default_factory=lambda: f"sec_{uuid.uuid4().hex[:12]}")
    incident_id: str | None = None  # link to the operational incident, when there is one
    kind: SecurityIncidentKind
    severity: Severity = Severity.HIGH
    status: IncidentStatus = IncidentStatus.DETECTED
    source_component: str = ""
    detector: str = ""
    correlation: CorrelationKeys = Field(default_factory=CorrelationKeys)
    containment: ContainmentAction = ContainmentAction.NONE
    contained_ts: str | None = None
    evidence_preserved: bool = False
    evidence: tuple[Evidence, ...] = ()
    # NEVER the payload itself — a redacted description of it. See store.py.
    indicator_summary: str = ""
    human_notified: bool = False
    first_seen_ts: str = Field(default_factory=_now)
    last_seen_ts: str = Field(default_factory=_now)
    resolved_ts: str | None = None
    notes: tuple[str, ...] = ()


# ---------------------------------------------------------------------------
# Root cause (§16, §57)


class RootCauseCandidate(BaseModel):
    """One possible cause, with what argues for AND against it.

    §16: "Never claim certainty without evidence." Enforced structurally —
    `confidence` is capped by the evidence actually attached (see
    `root_cause.py`), and `contradicting_evidence` is a first-class field so a
    candidate cannot quietly omit what disagrees with it.
    """

    model_config = ConfigDict(frozen=True)

    candidate_id: str = Field(default_factory=lambda: f"rcc_{uuid.uuid4().hex[:12]}")
    cause: str
    explanation: str = ""
    confidence: float = 0.0
    affected_components: tuple[str, ...] = ()
    supporting_evidence: tuple[Evidence, ...] = ()
    contradicting_evidence: tuple[Evidence, ...] = ()
    supporting_event_ids: tuple[str, ...] = ()
    recent_changes: tuple[str, ...] = ()  # §18: correlated, NOT declared causal
    analyzer: str = ""

    @property
    def evidence_count(self) -> int:
        return len(self.supporting_evidence)

    @property
    def is_contested(self) -> bool:
        return bool(self.contradicting_evidence)


class Diagnosis(BaseModel):
    """The ranked output of one diagnosis run over one incident."""

    model_config = ConfigDict(frozen=True)

    diagnosis_id: str = Field(default_factory=lambda: f"dx_{uuid.uuid4().hex[:12]}")
    incident_id: str
    candidates: tuple[RootCauseCandidate, ...] = ()
    method: str = ""
    passes_run: tuple[str, ...] = ()
    inconclusive_reason: str = ""  # non-empty when no candidate cleared the bar
    created_ts: str = Field(default_factory=_now)

    @property
    def primary(self) -> RootCauseCandidate | None:
        """Best candidate, or None. None is a legitimate outcome (§57)."""
        return self.candidates[0] if self.candidates else None


# ---------------------------------------------------------------------------
# Reproduction (§24)


class ReproductionMode(StrEnum):
    """§24: how a problem was reproduced.

    Every mode is side-effect free with respect to the outside world — §24 says
    "Do not reproduce dangerous external actions", so there is no LIVE mode and
    there must never be one.
    """

    DETERMINISTIC_REPRODUCTION = "DETERMINISTIC_REPRODUCTION"
    SANDBOX_REPRODUCTION = "SANDBOX_REPRODUCTION"
    REPLAY = "REPLAY"
    SIMULATION = "SIMULATION"
    LOG_RECONSTRUCTION = "LOG_RECONSTRUCTION"


class ReproductionResult(BaseModel):
    """Did we manage to make it happen again, and how confident is that?"""

    model_config = ConfigDict(frozen=True)

    incident_id: str
    mode: ReproductionMode
    reproduced: bool = False
    attempts: int = 0
    detail: str = ""
    artifact_ref: str = ""
    created_ts: str = Field(default_factory=_now)


# ---------------------------------------------------------------------------
# Repair (§27-§28, §34)


class RepairType(StrEnum):
    """§28: the nine repair classes.

    Note what is absent: there is no SAFETY_POLICY type. §28 says "Do not
    automatically mutate safety policy", and `adaptation.domain.ForbiddenChangeType`
    already enumerates SAFETY_ENGINE / PERMISSION_POLICIES / CREDENTIAL_RULES /
    SANDBOX_SECURITY / AUDIT_REQUIREMENTS as never-changeable. Rather than restate
    that list, this enum simply has no member that could express it.
    """

    CONFIG = "CONFIG"
    CODE = "CODE"
    DEPENDENCY = "DEPENDENCY"
    ROUTING = "ROUTING"
    STRATEGY = "STRATEGY"
    RETRIEVAL = "RETRIEVAL"
    PROVIDER_FAILOVER = "PROVIDER_FAILOVER"
    DATA = "DATA"
    UI_CONTRACT = "UI_CONTRACT"


#: §34: only these may ever be attempted without a human in the loop, and even
#: then only when severity, evidence, autonomy level and the security gate all
#: agree. Everything else is HUMAN_REVIEW_REQUIRED by construction.
#:
#: DATA is excluded on purpose — §47: "Do not allow autonomous data repair
#: without explicit safety controls". CODE is excluded because a code patch is
#: reviewed by a human in this build (see autonomy.py).
AUTO_REPAIR_ELIGIBLE_TYPES: frozenset[RepairType] = frozenset(
    {
        RepairType.CONFIG,
        RepairType.ROUTING,
        RepairType.PROVIDER_FAILOVER,
        RepairType.RETRIEVAL,
    }
)


class RepairStatus(StrEnum):
    PROPOSED = "PROPOSED"
    GATED = "GATED"  # awaiting the security/policy gate
    BLOCKED = "BLOCKED"  # gate said no; terminal without human action
    HUMAN_REVIEW_REQUIRED = "HUMAN_REVIEW_REQUIRED"
    APPROVED = "APPROVED"
    BUILDING = "BUILDING"
    TESTING = "TESTING"
    VERIFIED = "VERIFIED"
    PROMOTED = "PROMOTED"
    REJECTED = "REJECTED"
    ROLLED_BACK = "ROLLED_BACK"


class RepairHypothesis(BaseModel):
    """§27: a proposed fix, shaped after `adaptation.domain.Hypothesis`.

    Deliberately the same shape as the learning plane's hypothesis: a claim, the
    evidence behind it, the component it touches, the expected effect, and how it
    will be evaluated. The differences are the ones that matter for system repair
    — `repair_type`, the file paths it would touch, and the loop guard.
    """

    model_config = ConfigDict(frozen=True)

    repair_id: str = Field(default_factory=lambda: f"rep_{uuid.uuid4().hex[:12]}")
    incident_id: str
    diagnosis_id: str | None = None
    title: str
    problem_statement: str = ""
    proposed_change: str = ""
    repair_type: RepairType
    affected_component: str = ""
    target_paths: tuple[str, ...] = ()  # checked against the deny-list BEFORE any write
    expected_effect: str = ""
    risk: Literal["LOW", "MEDIUM", "HIGH"] = "MEDIUM"
    evidence: tuple[Evidence, ...] = ()
    verification_plan: str = ""
    status: RepairStatus = RepairStatus.PROPOSED
    gate_decision_id: str | None = None
    branch: str | None = None  # isolated worktree branch, keyed to incident_id (§30)
    # §85 loop guard: a repair caused by a repair is tracked, not hidden.
    repair_chain_id: str = Field(default_factory=lambda: f"chain_{uuid.uuid4().hex[:8]}")
    depth: int = 0
    parent_incident_id: str | None = None
    attempt: int = 1
    created_ts: str = Field(default_factory=_now)
    updated_ts: str = Field(default_factory=_now)


# ---------------------------------------------------------------------------
# Gate decisions (§33)


class GateVerdict(StrEnum):
    """§33: the security/policy gate's answer.

    HUMAN_REVIEW_REQUIRED is not a failure — it is the correct answer for every
    change class §33 lists (auth, credentials, permissions, network, sandbox,
    filesystem, tool execution, external comms).
    """

    ALLOW = "ALLOW"
    HUMAN_REVIEW_REQUIRED = "HUMAN_REVIEW_REQUIRED"
    DENY = "DENY"


class GateDecision(BaseModel):
    """One gate evaluation, recorded whether or not it allowed anything (§86)."""

    model_config = ConfigDict(frozen=True)

    decision_id: str = Field(default_factory=lambda: f"gate_{uuid.uuid4().hex[:12]}")
    repair_id: str
    verdict: GateVerdict
    reasons: tuple[str, ...] = ()
    blocked_paths: tuple[str, ...] = ()
    sensitive_areas: tuple[str, ...] = ()
    autonomy_level: int = 1
    severity: Severity = Severity.LOW
    created_ts: str = Field(default_factory=_now)


# ---------------------------------------------------------------------------
# Autonomy (§81)


class AutonomyLevel(IntEnum):
    """§81: six levels. Default is LEVEL_1/LEVEL_2 — see `autonomy.py`.

    An IntEnum because the gate genuinely needs `level >= X` comparisons, and
    encoding an ordering in strings is how ordering bugs happen.

    LEVEL_0  observe only — detect nothing, record nothing automatically
    LEVEL_1  detect and record; no diagnosis without an explicit request
    LEVEL_2  detect, diagnose and PROPOSE a repair; a human approves everything
    LEVEL_3  auto-repair eligible low-risk classes, verified, human notified
    LEVEL_4  auto-repair and auto-promote within limits
    LEVEL_5  reserved. Never the default, and not enabled by this build.
    """

    LEVEL_0 = 0
    LEVEL_1 = 1
    LEVEL_2 = 2
    LEVEL_3 = 3
    LEVEL_4 = 4
    LEVEL_5 = 5


#: §81: "default LEVEL 1/LEVEL 2". ATLAS detects, diagnoses and proposes; a human
#: approves. Raising this is an explicit operator action (§82).
DEFAULT_AUTONOMY_LEVEL = AutonomyLevel.LEVEL_2

#: §84: two failed repairs and we stop. A third attempt on the same incident is
#: not persistence, it is a loop.
MAX_REPAIR_ATTEMPTS = 2

#: §85: a repair whose own failure spawns a repair, twice over, is a loop.
MAX_REPAIR_CHAIN_DEPTH = 2

#: §16/§57: a candidate below this confidence never reaches the repair pipeline.
MIN_CONFIDENCE_FOR_REPAIR = 0.6

#: §13/§15: how many occurrences before a low-severity signal is worth a human's
#: attention. Mirrors `adaptation.domain.MIN_EVIDENCE_DEFAULT`.
MIN_OCCURRENCES_FOR_ESCALATION = 3


__all__ = [
    "ACTIVE_STATUSES",
    "AUTO_REPAIR_ELIGIBLE_TYPES",
    "DEFAULT_AUTONOMY_LEVEL",
    "MAX_REPAIR_ATTEMPTS",
    "MAX_REPAIR_CHAIN_DEPTH",
    "MIN_CONFIDENCE_FOR_REPAIR",
    "MIN_OCCURRENCES_FOR_ESCALATION",
    "TERMINAL_STATUSES",
    "AutonomyLevel",
    "ContainmentAction",
    "CorrelationKeys",
    "Diagnosis",
    "Evidence",
    "EvidenceKind",
    "GateDecision",
    "GateVerdict",
    "Incident",
    "IncidentSource",
    "IncidentStatus",
    "Measurement",
    "Provenance",
    "RepairHypothesis",
    "RepairStatus",
    "RepairType",
    "ReproductionMode",
    "ReproductionResult",
    "RootCauseCandidate",
    "SecurityIncident",
    "SecurityIncidentKind",
    "Severity",
    "max_severity",
    "severity_rank",
]
