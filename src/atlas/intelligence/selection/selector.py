"""Model selection engine — capabilities + constraints + live signals -> ranked.

WHY multi-factor scoring (not 'pick the smartest'): the best model is the one
that satisfies capabilities within budget, is healthy, meets latency, and has
good historical reliability. Score blends quality, reliability, cost, latency,
and health, each normalized. Returns a RANKED list so the fallback engine has an
ordered chain for free.
"""

from __future__ import annotations

from atlas.intelligence.capabilities import CapabilitySet
from atlas.intelligence.contracts import Constraints, ModelSpec
from atlas.intelligence.errors import RoutingError
from atlas.intelligence.health.health_monitor import HealthMonitor
from atlas.intelligence.registry.capability_index import CapabilityIndex


class ModelSelector:
    def __init__(self, index: CapabilityIndex, health: HealthMonitor) -> None:
        self._index = index
        self._health = health

    def select(self, required: CapabilitySet, constraints: Constraints) -> list[ModelSpec]:
        candidates = self._index.candidates(required)
        candidates = [c for c in candidates if self._passes(c, constraints)]
        if constraints.pinned_model:
            candidates = [c for c in candidates if c.id == constraints.pinned_model] or candidates
        if not candidates:
            raise RoutingError(f"no model satisfies {sorted(c.value for c in required)}")
        ranked = sorted(candidates, key=lambda m: self._score(m, constraints), reverse=True)
        return ranked

    def _passes(self, m: ModelSpec, c: Constraints) -> bool:
        if c.min_context and m.context_length < c.min_context:
            return False
        if c.require_streaming and not m.supports_streaming:
            return False
        if c.max_latency_ms and m.latency_estimate_ms > c.max_latency_ms:
            return False
        if not self._health.is_available(m.provider):
            return False
        return True

    def _score(self, m: ModelSpec, c: Constraints) -> float:
        # normalized blend; weights are explicit and tunable.
        quality = m.quality_score
        reliability = m.reliability_score
        cost_pen = 1.0 / (1.0 + m.usd_per_1m_output)      # cheaper = higher
        latency_pen = 1.0 / (1.0 + m.latency_estimate_ms / 1000.0)
        local_bonus = 0.15 if (c.prefer_local and m.usd_per_1m_output == 0) else 0.0
        health = self._health.reliability(m.provider)
        return (0.35 * quality + 0.20 * reliability + 0.15 * cost_pen
                + 0.10 * latency_pen + 0.20 * health + local_bonus)
