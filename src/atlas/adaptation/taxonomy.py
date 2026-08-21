"""Failure taxonomy for the adaptation plane (Prompt 4 §6).

The 23-class taxonomy is FINER than `memory.trajectory.FailureCategory`
(9 coarse classes). Trajectory failures keep their coarse category for
runtime bookkeeping; the adaptation plane re-classifies them here so the
FailureAnalyzer can separate symptom from root cause (§7).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from atlas.infra.clock import Clock, SystemClock


class FailureClass(StrEnum):
    """The 23 §6 failure classes."""

    INTENT_FAILURE = "INTENT_FAILURE"
    PLANNING_FAILURE = "PLANNING_FAILURE"
    REASONING_FAILURE = "REASONING_FAILURE"
    CAPABILITY_SELECTION_FAILURE = "CAPABILITY_SELECTION_FAILURE"
    TOOL_SELECTION_FAILURE = "TOOL_SELECTION_FAILURE"
    TOOL_EXECUTION_FAILURE = "TOOL_EXECUTION_FAILURE"
    PERCEPTION_FAILURE = "PERCEPTION_FAILURE"
    TARGET_GROUNDING_FAILURE = "TARGET_GROUNDING_FAILURE"
    RETRIEVAL_FAILURE = "RETRIEVAL_FAILURE"
    RERANK_FAILURE = "RERANK_FAILURE"
    KNOWLEDGE_FAILURE = "KNOWLEDGE_FAILURE"
    MEMORY_FAILURE = "MEMORY_FAILURE"
    MODEL_FAILURE = "MODEL_FAILURE"
    CONTEXT_FAILURE = "CONTEXT_FAILURE"
    VERIFICATION_FAILURE = "VERIFICATION_FAILURE"
    RECOVERY_FAILURE = "RECOVERY_FAILURE"
    RESOURCE_FAILURE = "RESOURCE_FAILURE"
    TIMEOUT = "TIMEOUT"
    BUDGET_FAILURE = "BUDGET_FAILURE"
    SAFETY_BLOCK = "SAFETY_BLOCK"
    AUTH_FAILURE = "AUTH_FAILURE"
    USER_CONSTRAINT_FAILURE = "USER_CONSTRAINT_FAILURE"
    ENVIRONMENT_FAILURE = "ENVIRONMENT_FAILURE"


class FailureDomain(StrEnum):
    """Coarse domain a failure belongs to — used for deterministic clustering."""

    PLANNING = "PLANNING"
    EXECUTION = "EXECUTION"
    KNOWLEDGE = "KNOWLEDGE"
    PERCEPTION = "PERCEPTION"
    MODEL = "MODEL"
    SAFETY = "SAFETY"
    RESOURCE = "RESOURCE"
    ENVIRONMENT = "ENVIRONMENT"


_FAILURE_DOMAIN: dict[FailureClass, FailureDomain] = {
    FailureClass.INTENT_FAILURE: FailureDomain.PLANNING,
    FailureClass.PLANNING_FAILURE: FailureDomain.PLANNING,
    FailureClass.REASONING_FAILURE: FailureDomain.MODEL,
    FailureClass.CAPABILITY_SELECTION_FAILURE: FailureDomain.PLANNING,
    FailureClass.TOOL_SELECTION_FAILURE: FailureDomain.PLANNING,
    FailureClass.TOOL_EXECUTION_FAILURE: FailureDomain.EXECUTION,
    FailureClass.PERCEPTION_FAILURE: FailureDomain.PERCEPTION,
    FailureClass.TARGET_GROUNDING_FAILURE: FailureDomain.PERCEPTION,
    FailureClass.RETRIEVAL_FAILURE: FailureDomain.KNOWLEDGE,
    FailureClass.RERANK_FAILURE: FailureDomain.KNOWLEDGE,
    FailureClass.KNOWLEDGE_FAILURE: FailureDomain.KNOWLEDGE,
    FailureClass.MEMORY_FAILURE: FailureDomain.KNOWLEDGE,
    FailureClass.MODEL_FAILURE: FailureDomain.MODEL,
    FailureClass.CONTEXT_FAILURE: FailureDomain.MODEL,
    FailureClass.VERIFICATION_FAILURE: FailureDomain.EXECUTION,
    FailureClass.RECOVERY_FAILURE: FailureDomain.EXECUTION,
    FailureClass.RESOURCE_FAILURE: FailureDomain.RESOURCE,
    FailureClass.TIMEOUT: FailureDomain.RESOURCE,
    FailureClass.BUDGET_FAILURE: FailureDomain.RESOURCE,
    FailureClass.SAFETY_BLOCK: FailureDomain.SAFETY,
    FailureClass.AUTH_FAILURE: FailureDomain.ENVIRONMENT,
    FailureClass.USER_CONSTRAINT_FAILURE: FailureDomain.ENVIRONMENT,
    FailureClass.ENVIRONMENT_FAILURE: FailureDomain.ENVIRONMENT,
}


def domain_of(failure_class: FailureClass) -> FailureDomain:
    """Deterministic mapping of a failure class onto its coarse domain."""
    return _FAILURE_DOMAIN[failure_class]


class FailureTaxonomy(BaseModel):
    """One classified failure attached to a trajectory (§6).

    `root_cause_candidate` marks whether this record is believed to be the
    root cause or merely the symptom; the FailureAnalyzer refines this.
    """

    model_config = ConfigDict(frozen=True)

    failure_id: str = Field(default_factory=lambda: f"flt_{uuid.uuid4().hex[:12]}")
    trajectory_id: str
    failure_class: FailureClass
    step_id: int | None = None
    evidence: tuple[str, ...] = ()
    root_cause_candidate: bool = False
    recoverable: bool = False
    recovery_attempts: int = 0
    final_resolution: Literal["RECOVERED", "FAILED", "ESCALATED", "ABANDONED"] = "FAILED"
    created_ts: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())

    @classmethod
    def create(
        cls,
        trajectory_id: str,
        failure_class: FailureClass,
        *,
        step_id: int | None = None,
        evidence: tuple[str, ...] = (),
        root_cause_candidate: bool = False,
        recoverable: bool = False,
        recovery_attempts: int = 0,
        final_resolution: Literal["RECOVERED", "FAILED", "ESCALATED", "ABANDONED"] = "FAILED",
        clock: Clock | None = None,
    ) -> FailureTaxonomy:
        return cls(
            trajectory_id=trajectory_id,
            failure_class=failure_class,
            step_id=step_id,
            evidence=evidence,
            root_cause_candidate=root_cause_candidate,
            recoverable=recoverable,
            recovery_attempts=recovery_attempts,
            final_resolution=final_resolution,
            created_ts=(clock or SystemClock()).now().isoformat(),
        )
