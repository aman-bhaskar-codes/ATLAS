"""Capability health — per-provider rolling stats + circuit breaker.

WHY reuse the Phase-5 CircuitBreaker: identical semantics everywhere (open after
N failures, half-open after cooldown). is_available() gates provider selection;
reliability() orders equally-preferred providers. This is how unhealthy providers
auto-downgrade with zero orchestrator involvement.
"""

from __future__ import annotations

from collections import defaultdict, deque

from atlas.intelligence.governance.circuit_breaker import CircuitBreaker


class CapabilityHealth:
    def __init__(self, window: int = 50) -> None:
        self._ok: dict[str, deque[bool]] = defaultdict(lambda: deque(maxlen=window))
        self._latency: dict[str, deque[int]] = defaultdict(lambda: deque(maxlen=window))
        self._breakers: dict[str, CircuitBreaker] = defaultdict(CircuitBreaker)

    def record(self, provider: str, *, ok: bool, latency_ms: int) -> None:
        self._ok[provider].append(ok)
        self._latency[provider].append(latency_ms)
        if ok:
            self._breakers[provider].record_success()
        else:
            self._breakers[provider].record_failure()

    def is_available(self, provider: str) -> bool:
        return self._breakers[provider].allow()

    def reliability(self, provider: str) -> float:
        r = self._ok[provider]
        return sum(r) / len(r) if r else 1.0

    def snapshot(self) -> dict[str, dict[str, float]]:
        out: dict[str, dict[str, float]] = {}
        for p in self._ok:
            lat = self._latency[p]
            out[p] = {
                "reliability": self.reliability(p),
                "avg_latency_ms": (sum(lat) / len(lat)) if lat else 0.0,
                "available": 1.0 if self.is_available(p) else 0.0,
            }
        return out
