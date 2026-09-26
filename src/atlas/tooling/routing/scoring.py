"""L7 deterministic ranking + judgment composition (Part 3 §33/§34).

``final_score = deterministic_score + bounded_judgment_signal`` (§34), with
the per-component breakdown recorded on the decision so every route explains
exactly how much each signal contributed. Same inputs → same ranking (§66).
"""

from __future__ import annotations

from dataclasses import dataclass

from atlas.tooling.routing.config import ScoringWeightsCfg
from atlas.tooling.routing.models import CandidateType, RouteCandidate, TaskIR

_PRIVACY_RANK = {"public": 0, "internal": 1, "private": 2, "sensitive": 3, "secret": 4}
_COST_PREFERENCE = {"local": 1.0, "free": 0.9, "free_quota": 0.7, "paid": 0.0}
_TRUST_SCORE = {
    "system_builtin": 1.0,
    "user_configured": 0.9,
    "local_mcp": 0.8,
    "trusted_remote": 0.7,
    "third_party_remote": 0.4,
    "unknown": 0.0,
}


@dataclass(frozen=True)
class ScoredCandidate:
    candidate: RouteCandidate
    total: float
    components: dict[str, float]


def deterministic_score(
    candidate: RouteCandidate,
    task: TaskIR,
    *,
    required_capability: str | None = None,
    required_operation: str | None = None,
    weights: ScoringWeightsCfg | None = None,
) -> ScoredCandidate:
    """Deterministic scoring dimensions (§33), 0..1 each, weighted by config."""
    w = weights or ScoringWeightsCfg()
    components: dict[str, float] = {}

    # Capability fit: exact capability > capability in tags > neutral.
    if required_capability:
        if candidate.capability == required_capability or required_capability in candidate.tags:
            components["capability_fit"] = 1.0
        elif candidate.candidate_type != CandidateType.TOOL:
            components["capability_fit"] = 0.5
        else:
            components["capability_fit"] = 0.3
    else:
        components["capability_fit"] = 0.7

    # Operation fit: exact operation support.
    if required_operation:
        components["operation_fit"] = 1.0 if required_operation in candidate.operations else 0.0
    else:
        components["operation_fit"] = 0.7

    # Availability (post-hard-filter all survivors are live, but degraded states rank lower).
    components["availability"] = 1.0 if candidate.availability == "AVAILABLE" else 0.4

    # Latency: cheaper-is-better curve around 500ms.
    components["latency"] = max(0.0, 1.0 - candidate.estimated_latency_ms / 5000.0)

    # Cost: free-first preference (§36 Part 1 vocabulary).
    components["cost"] = _COST_PREFERENCE.get(candidate.cost_class, 0.5)

    # Locality: local preferred when the policy allows it.
    components["locality"] = 1.0 if candidate.locality == "local" else 0.6

    # Privacy headroom: candidates with a higher ceiling fit more tasks.
    components["privacy"] = _PRIVACY_RANK.get(candidate.privacy_class, 0) / 4.0

    # Trust.
    components["trust"] = _TRUST_SCORE.get(candidate.trust_level, 0.0)

    # Idempotency (slight preference for safely retryable candidates).
    components["idempotency"] = 1.0 if candidate.idempotent else 0.5

    total = (
        components["capability_fit"] * w.capability_fit
        + components["operation_fit"] * w.operation_fit
        + components["availability"] * w.availability
        + components["latency"] * w.latency
        + components["cost"] * w.cost
        + components["locality"] * w.locality
        + components["privacy"] * w.privacy
        + components["trust"] * w.trust
        + components["idempotency"] * w.idempotency
    )
    weight_sum = (
        w.capability_fit
        + w.operation_fit
        + w.availability
        + w.latency
        + w.cost
        + w.locality
        + w.privacy
        + w.trust
        + w.idempotency
    )
    return ScoredCandidate(candidate=candidate, total=round(total / weight_sum, 4), components=components)


def compose_final_score(
    deterministic: ScoredCandidate,
    judgment_score: float | None,
    weights: ScoringWeightsCfg | None = None,
) -> ScoredCandidate:
    """§34: judgment is a BOUNDED ADDITIVE SIGNAL on top of the deterministic
    score, never the whole score. Breakdown preserved for the explanation."""
    w = weights or ScoringWeightsCfg()
    components = dict(deterministic.components)
    total = deterministic.total
    if judgment_score is not None:
        # bounded: judgment contributes at most `w.judgment / (sum + w.judgment)`
        # of the final normalized score.
        weight_sum = 1.0 + w.judgment
        total = (deterministic.total * 1.0 + judgment_score * w.judgment) / weight_sum
        components["judgment"] = judgment_score
    return ScoredCandidate(
        candidate=deterministic.candidate,
        total=round(max(0.0, min(1.0, total)), 4),
        components=components,
    )


def rank(
    scored: list[ScoredCandidate],
) -> list[ScoredCandidate]:
    """Deterministic order: score desc, then candidate_id asc (§66)."""
    return sorted(scored, key=lambda s: (-s.total, s.candidate.candidate_id))


__all__ = ["ScoredCandidate", "compose_final_score", "deterministic_score", "rank"]
