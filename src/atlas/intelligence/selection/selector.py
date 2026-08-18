"""Model selection engine — capabilities + constraints + live signals -> ranked.

WHY multi-factor scoring (not 'pick the smartest'): the best model is the one
that satisfies capabilities within budget, is healthy, meets latency, and has
good historical reliability. Score blends quality, reliability, cost, latency,
and health, each normalized. Returns a RANKED list so the fallback engine has an
ordered chain for free.

ZERO-COST-FIRST ADDITIONS (Phase 1):
 - _passes() now enforces CostPolicy, NetworkPolicy, and PrivacyClass as
   hard filters BEFORE scoring. A model that violates policy is never scored.
 - _score() applies stronger local_bonus when cost_policy is FREE_PREFERRED.
 - The selector is purely deterministic and observable: every filter decision
   is traceable from the constraints + model metadata.
"""

from __future__ import annotations

from atlas.infra.types import CostClass, CostPolicy, NetworkPolicy, PrivacyClass
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
        # --- Existing filters ---
        if c.min_context and m.context_length < c.min_context:
            return False
        if c.require_streaming and not m.supports_streaming:
            return False
        if c.max_latency_ms and m.latency_estimate_ms > c.max_latency_ms:
            return False
        if not self._health.is_available(m.provider):
            return False

        # --- Zero-Cost-First policy filters ---

        # Cost policy enforcement: ZERO_COST and FREE_ONLY hard-block PAID models
        if c.cost_policy == CostPolicy.ZERO_COST:
            if m.cost_class == CostClass.PAID:
                return False
        elif c.cost_policy == CostPolicy.FREE_ONLY:
            if m.cost_class == CostClass.PAID:
                return False

        # Network policy enforcement
        if c.network_policy == NetworkPolicy.OFFLINE:
            # Offline: only local models (Ollama etc.) — no network at all
            if m.cost_class != CostClass.LOCAL:
                return False
        elif c.network_policy == NetworkPolicy.LOCAL_ONLY:
            # Local-only: no external API calls (but LAN services like Ollama OK)
            if m.cost_class != CostClass.LOCAL:
                return False
        elif c.network_policy == NetworkPolicy.FREE_CLOUD:
            # Free-cloud: block paid providers, allow local + free
            if m.cost_class == CostClass.PAID:
                return False

        # Privacy classification enforcement
        if c.privacy_class == PrivacyClass.SECRET:
            # SECRET data must never leave local hardware
            if m.cost_class != CostClass.LOCAL:
                return False
        elif c.privacy_class == PrivacyClass.SENSITIVE:
            # SENSITIVE: strongly prefer local, block paid cloud
            if m.cost_class == CostClass.PAID:
                return False

        return True

    def _score(self, m: ModelSpec, c: Constraints) -> float:
        # normalized blend; weights are explicit and tunable.
        quality = m.quality_score
        reliability = m.reliability_score
        cost_pen = 1.0 / (1.0 + m.usd_per_1m_output)  # cheaper = higher
        latency_pen = 1.0 / (1.0 + m.latency_estimate_ms / 1000.0)
        health = self._health.reliability(m.provider)

        # Local bonus: stronger when cost policy is free-preferred or zero-cost
        local_bonus = 0.0
        if m.cost_class == CostClass.LOCAL:
            if c.cost_policy in (CostPolicy.ZERO_COST, CostPolicy.FREE_ONLY, CostPolicy.FREE_PREFERRED):
                local_bonus = 0.25  # strong preference for local
            elif c.prefer_local:
                local_bonus = 0.15  # original behavior

        # Free-quota models get a smaller bonus in free-preferred mode
        free_bonus = 0.0
        if m.cost_class == CostClass.FREE_QUOTA and c.cost_policy in (
            CostPolicy.FREE_PREFERRED,
            CostPolicy.FREE_ONLY,
        ):
            free_bonus = 0.10

        # Privacy bonus: prefer local for private/sensitive data
        privacy_bonus = 0.0
        if c.privacy_class in (PrivacyClass.PRIVATE, PrivacyClass.SENSITIVE):
            if m.cost_class == CostClass.LOCAL:
                privacy_bonus = 0.10

        return (
            0.30 * quality
            + 0.20 * reliability
            + 0.15 * cost_pen
            + 0.10 * latency_pen
            + 0.15 * health
            + local_bonus
            + free_bonus
            + privacy_bonus
        )
