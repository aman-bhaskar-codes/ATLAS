"""Causal failure analysis (Prompt 4 §7).

"Do not simply classify a failure by the last exception." The last error is
the SYMPTOM; the root cause usually sits earlier in the trajectory — a bad
plan leads to a wrong file leads to a wrong tool leads to a tool error.

The analyzer is DETERMINISTIC: it walks the decision traces and failure
records backwards from the symptom and attributes the failure to the earliest
decision/state that explains it. No LLM calls, zero cost, fully testable.
"""

from __future__ import annotations

from atlas.adaptation.domain import FailureAnalysis
from atlas.adaptation.taxonomy import FailureClass
from atlas.infra.clock import Clock, SystemClock
from atlas.infra.logging import get_logger
from atlas.memory.trajectory import (
    DecisionOutcome,
    DecisionPoint,
    DecisionTrace,
    FailureCategory,
    FailureRecord,
    Trajectory,
)

_log = get_logger("atlas.adaptation.analyzer")

# Coarse runtime category → taxonomy symptom class
_SYMPTOM_CLASS: dict[FailureCategory, FailureClass] = {
    FailureCategory.TOOL_ERROR: FailureClass.TOOL_EXECUTION_FAILURE,
    FailureCategory.MODEL_ERROR: FailureClass.MODEL_FAILURE,
    FailureCategory.PLANNING_ERROR: FailureClass.PLANNING_FAILURE,
    FailureCategory.VERIFICATION_FAILED: FailureClass.VERIFICATION_FAILURE,
    FailureCategory.TIMEOUT: FailureClass.TIMEOUT,
    FailureCategory.CANCELLATION: FailureClass.ENVIRONMENT_FAILURE,
    FailureCategory.SAFETY_BLOCK: FailureClass.SAFETY_BLOCK,
    FailureCategory.RESOURCE_EXHAUSTION: FailureClass.RESOURCE_FAILURE,
    FailureCategory.UNKNOWN: FailureClass.ENVIRONMENT_FAILURE,
}

# Decision point that failed → the decision-level root cause class
_DECISION_CAUSE: dict[DecisionPoint, FailureClass] = {
    DecisionPoint.PLANNING: FailureClass.PLANNING_FAILURE,
    DecisionPoint.REPLANNING: FailureClass.PLANNING_FAILURE,
    DecisionPoint.MODEL_SELECTION: FailureClass.MODEL_FAILURE,
    DecisionPoint.TOOL_SELECTION: FailureClass.TOOL_SELECTION_FAILURE,
    DecisionPoint.ROUTING: FailureClass.CAPABILITY_SELECTION_FAILURE,
    DecisionPoint.VERIFICATION: FailureClass.VERIFICATION_FAILURE,
    DecisionPoint.CRITIQUE: FailureClass.REASONING_FAILURE,
}

# Root cause → recommended intervention (deterministic, actionable)
_INTERVENTIONS: dict[FailureClass, str] = {
    FailureClass.INTENT_FAILURE: "clarify goal extraction before planning",
    FailureClass.PLANNING_FAILURE: "prefer proven strategy for this task class",
    FailureClass.REASONING_FAILURE: "tighten critique loop termination criteria",
    FailureClass.CAPABILITY_SELECTION_FAILURE: "re-rank capabilities by measured success rate",
    FailureClass.TOOL_SELECTION_FAILURE: "rank tools by per-task-type evidence before selection",
    FailureClass.TOOL_EXECUTION_FAILURE: "verify tool preconditions before invocation",
    FailureClass.PERCEPTION_FAILURE: "refresh perception snapshot before acting",
    FailureClass.TARGET_GROUNDING_FAILURE: "re-locate UI target with fresh snapshot before retry",
    FailureClass.RETRIEVAL_FAILURE: "rewrite query and broaden source set",
    FailureClass.RERANK_FAILURE: "increase candidate pool before reranking",
    FailureClass.KNOWLEDGE_FAILURE: "escalate to web research provider",
    FailureClass.MEMORY_FAILURE: "re-index memory before retrying retrieval",
    FailureClass.MODEL_FAILURE: "route to fallback model with stronger output discipline",
    FailureClass.CONTEXT_FAILURE: "compact and re-compile context window",
    FailureClass.VERIFICATION_FAILURE: "run deterministic checks before LLM verification",
    FailureClass.RECOVERY_FAILURE: "escalate to human instead of retrying same recovery",
    FailureClass.RESOURCE_FAILURE: "raise step/token budget or split task",
    FailureClass.TIMEOUT: "split long-running step into checkpointed subtasks",
    FailureClass.BUDGET_FAILURE: "apply cheaper strategy tier for this task class",
    FailureClass.SAFETY_BLOCK: "surface block to user; never attempt to bypass safety",
    FailureClass.AUTH_FAILURE: "refresh credentials via identity platform",
    FailureClass.USER_CONSTRAINT_FAILURE: "re-read user constraints into plan context",
    FailureClass.ENVIRONMENT_FAILURE: "retry after environment health check",
}


class FailureAnalyzer:
    """Symptom-vs-root-cause analysis for one failed trajectory."""

    def __init__(self, *, clock: Clock | None = None) -> None:
        self._clock = clock or SystemClock()

    def analyze(
        self,
        trajectory: Trajectory,
        decision_traces: tuple[DecisionTrace, ...] = (),
        failure_records: tuple[FailureRecord, ...] = (),
    ) -> FailureAnalysis:
        evidence: list[str] = []
        secondary: list[FailureClass] = []

        # Symptom: the coarse runtime classification of the final failure.
        symptom = FailureClass.ENVIRONMENT_FAILURE
        if failure_records:
            symptom = _SYMPTOM_CLASS[failure_records[-1].category]
            for record in failure_records:
                evidence.append(f"failure:{record.id}:{record.component}:{record.error_message[:120]}")
        elif trajectory.error:
            evidence.append(f"trajectory.error:{trajectory.error[:120]}")

        # Root-cause walk: earliest FAILED/SUBOPTIMAL decision explains the
        # failure better than the last exception.
        primary = symptom
        confidence = 0.3
        avoidable = False
        failed_decisions = [
            t for t in decision_traces if t.outcome in (DecisionOutcome.FAILURE, DecisionOutcome.SUBOPTIMAL)
        ]
        for trace in sorted(failed_decisions, key=lambda t: t.ts):
            cause = _DECISION_CAUSE.get(trace.decision_point)
            if cause is None or trace.decision_point is DecisionPoint.SAFETY_TIER:
                # Safety blocks are policy outcomes, never adaptation targets.
                continue
            evidence.append(f"decision:{trace.id}:{trace.decision_point.value}:{trace.chosen_option}")
            if primary is symptom:  # first explaining decision becomes primary
                primary = cause
                confidence = 0.6
                avoidable = True
            elif cause not in secondary and cause is not primary:
                secondary.append(cause)

        # Structural signals on the trajectory itself.
        if trajectory.replan_count >= 2 and primary is symptom:
            primary = FailureClass.PLANNING_FAILURE
            confidence = max(confidence, 0.55)
            avoidable = True
            evidence.append(f"replans:{trajectory.replan_count}")
        if trajectory.verification_passed is False and trajectory.success:
            secondary.append(FailureClass.VERIFICATION_FAILURE)
            evidence.append("verification.failed_but_success")
        if trajectory.model_calls == 0 and not trajectory.success and primary is symptom:
            primary = FailureClass.ENVIRONMENT_FAILURE
            evidence.append("no_model_calls")

        # Many distinct failed decisions → strong root-cause evidence.
        if len(failed_decisions) >= 2:
            confidence = min(0.9, confidence + 0.1 * (len(failed_decisions) - 1))

        analysis = FailureAnalysis(
            trajectory_id=trajectory.id,
            primary_cause=primary,
            secondary_causes=tuple(dict.fromkeys(secondary)),
            evidence=tuple(evidence),
            confidence=round(confidence, 3),
            avoidable=avoidable,
            recommended_intervention=_INTERVENTIONS[primary],
            created_ts=self._clock.now().isoformat(),
        )
        _log.info(
            "failure.analyzed",
            event_type="adaptation",
            trajectory_id=trajectory.id,
            primary=primary.value,
            symptom=symptom.value,
            avoidable=avoidable,
        )
        return analysis


__all__ = ["FailureAnalyzer"]
