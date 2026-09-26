"""L9 recovery routing — failure classification + recovery graph (§51-§53).

Extends the existing failure vocabulary rather than duplicating it (§51): the
tooling/capability error taxonomy classifies the IMMEDIATE failure; this
module maps those outcomes to routing-level ``FailureCategory`` values and
decides the next recovery step DOWN A GRAPH, with hard bounds (§50/§84:
recovery can never exceed retry/replan limits — exhaustion terminates).
"""

from __future__ import annotations

from atlas.tooling.routing.models import (
    FailureCategory,
    RecoveryAction,
    RecoveryDecision,
)

#: §52: per-category recovery ladder, tried in order. BOUNDS are enforced by
#: RecoveryRouter using the configured max_retries/max_replans.
_RECOVERY_LADDERS: dict[FailureCategory, tuple[RecoveryAction, ...]] = {
    FailureCategory.TIMEOUT: (
        RecoveryAction.RETRY,
        RecoveryAction.ALTERNATE_PROVIDER,
        RecoveryAction.ALTERNATE_CANDIDATE,
        RecoveryAction.REPLAN,
    ),
    FailureCategory.UNAVAILABLE: (
        RecoveryAction.ALTERNATE_PROVIDER,
        RecoveryAction.ALTERNATE_CANDIDATE,
        RecoveryAction.REPLAN,
    ),
    FailureCategory.RATE_LIMIT: (
        RecoveryAction.RETRY,
        RecoveryAction.ALTERNATE_PROVIDER,
        RecoveryAction.REPLAN,
    ),
    FailureCategory.QUOTA: (
        RecoveryAction.ALTERNATE_PROVIDER,
        RecoveryAction.ALTERNATE_CANDIDATE,
        RecoveryAction.REPLAN,
    ),
    FailureCategory.AUTH: (
        RecoveryAction.ALTERNATE_PROVIDER,
        RecoveryAction.ESCALATE_HUMAN,
    ),
    FailureCategory.BAD_ARGUMENT: (
        RecoveryAction.RETRY_WITH_CHANGED_PARAMETERS,
        RecoveryAction.REPLAN,
    ),
    FailureCategory.SCHEMA: (
        RecoveryAction.RETRY_WITH_CHANGED_PARAMETERS,
        RecoveryAction.ALTERNATE_CANDIDATE,
    ),
    FailureCategory.BAD_RESULT: (
        RecoveryAction.VALIDATE_RESULT,
        RecoveryAction.RETRY_WITH_CHANGED_PARAMETERS,
        RecoveryAction.ALTERNATE_CANDIDATE,
    ),
    FailureCategory.ENVIRONMENT: (
        RecoveryAction.RETRY,
        RecoveryAction.ALTERNATE_CANDIDATE,
        RecoveryAction.ESCALATE_HUMAN,
    ),
    FailureCategory.AGENT_STALL: (RecoveryAction.REPLAN, RecoveryAction.ESCALATE_HUMAN),
    FailureCategory.GOAL_MISMATCH: (RecoveryAction.REPLAN,),
    FailureCategory.VERIFICATION_FAILED: (
        RecoveryAction.VALIDATE_RESULT,
        RecoveryAction.REPLAN,
        RecoveryAction.ESCALATE_HUMAN,
    ),
    FailureCategory.UNKNOWN: (
        RecoveryAction.RETRY,
        RecoveryAction.ALTERNATE_CANDIDATE,
        RecoveryAction.REPLAN,
    ),
}


def classify_failure(
    *,
    error: str | None = None,
    failure_kind: str | None = None,
    status_code: int | None = None,
) -> FailureCategory:
    """Map an execution outcome to a routing FailureCategory (§51).

    ``failure_kind`` is the tooling ``FailureKind``/capability error class name;
    ``error`` is the human message — classified by LAST match on explicit
    markers, never by guessing.
    """
    kind = (failure_kind or "").lower()
    message = (error or "").lower()
    if "timeout" in kind or "timed out" in message:
        return FailureCategory.TIMEOUT
    if ("rate" in kind and "limit" in kind) or "rate limited" in message or status_code == 429:
        return FailureCategory.RATE_LIMIT
    if "quota" in kind or "quota" in message or status_code == 402:
        return FailureCategory.QUOTA
    if "auth" in kind or "credential" in message or status_code in (401, 403):
        return FailureCategory.AUTH
    if "schema" in kind or "validation" in kind:
        return FailureCategory.SCHEMA
    if "unavailable" in kind or "no provider" in message or "not registered" in message:
        return FailureCategory.UNAVAILABLE
    if "policy_denied" in kind or "denied" in message or "halted" in kind:
        # A safety denial is not a recoverable routing failure — it is terminal
        # at this layer; the SafetyEngine decision stands (§21/§31).
        return FailureCategory.GOAL_MISMATCH
    if "stall" in kind:
        return FailureCategory.AGENT_STALL
    if "verification" in kind:
        return FailureCategory.VERIFICATION_FAILED
    if "environment" in kind or "sandbox" in message:
        return FailureCategory.ENVIRONMENT
    if "bad_argument" in kind or "invalid" in message:
        return FailureCategory.BAD_ARGUMENT
    return FailureCategory.UNKNOWN


class RecoveryRouter:
    """§52/§53: walks the per-category recovery ladder as a GRAPH — each step
    is a RecoveryDecision; bounds are hard (§84)."""

    def __init__(self, *, max_retries: int = 3, max_replans: int = 3) -> None:
        self._max_retries = max_retries
        self._max_replans = max_replans
        self._retry_counts: dict[str, int] = {}
        self._replan_counts: dict[str, int] = {}

    def next_recovery(
        self,
        *,
        route_id: str,
        failure: FailureCategory,
        attempt: int,
        fallback_candidates: tuple[str, ...] = (),
        fallback_cursor: int = 0,
    ) -> RecoveryDecision:
        ladder = _RECOVERY_LADDERS[failure]
        # Pick the first ladder action that is still within bounds.
        for action in ladder:
            if action == RecoveryAction.RETRY:
                if attempt <= self._max_retries:
                    return RecoveryDecision(
                        failure_category=failure,
                        action=action,
                        attempt=attempt,
                        max_attempts=self._max_retries,
                        reason=f"retry {attempt}/{self._max_retries} after {failure.value}",
                    )
                continue
            if action in (RecoveryAction.ALTERNATE_PROVIDER, RecoveryAction.ALTERNATE_CANDIDATE):
                if fallback_cursor < len(fallback_candidates):
                    return RecoveryDecision(
                        failure_category=failure,
                        action=RecoveryAction.ALTERNATE_CANDIDATE,
                        candidate_id=fallback_candidates[fallback_cursor],
                        attempt=attempt,
                        max_attempts=self._max_retries,
                        reason=f"fall back to {fallback_candidates[fallback_cursor]} after {failure.value}",
                    )
                continue
            if action == RecoveryAction.RETRY_WITH_CHANGED_PARAMETERS:
                if attempt <= self._max_retries:
                    return RecoveryDecision(
                        failure_category=failure,
                        action=action,
                        attempt=attempt,
                        max_attempts=self._max_retries,
                        reason=f"adjust parameters and retry after {failure.value}",
                    )
                continue
            if action == RecoveryAction.VALIDATE_RESULT:
                return RecoveryDecision(
                    failure_category=failure,
                    action=action,
                    attempt=attempt,
                    max_attempts=self._max_retries,
                    reason=f"validate the actual result before further recovery ({failure.value})",
                )
            if action == RecoveryAction.REPLAN:
                replans = self._replan_counts.get(route_id, 0) + 1
                if replans <= self._max_replans:
                    self._replan_counts[route_id] = replans
                    return RecoveryDecision(
                        failure_category=failure,
                        action=action,
                        attempt=attempt,
                        max_attempts=self._max_retries,
                        reason=f"replan (attempt {replans}/{self._max_replans}) after {failure.value}",
                    )
                continue
            if action == RecoveryAction.ESCALATE_HUMAN:
                return RecoveryDecision(
                    failure_category=failure,
                    action=action,
                    attempt=attempt,
                    max_attempts=self._max_retries,
                    reason=f"{failure.value} requires human action",
                )
        # Every ladder rung exhausted → terminate (§84: bounded recovery).
        return RecoveryDecision(
            failure_category=failure,
            action=RecoveryAction.TERMINATE,
            attempt=attempt,
            max_attempts=self._max_retries,
            exhausted=True,
            reason=f"recovery ladder exhausted for {failure.value}",
        )

    def reset(self, route_id: str) -> None:
        self._replan_counts.pop(route_id, None)
        self._retry_counts.pop(route_id, None)


__all__ = ["FailureCategory", "RecoveryRouter", "classify_failure"]
