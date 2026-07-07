"""Provider health monitor — rolling availability/latency/failure + breaker.

WHY it owns the breaker + feeds reliability into selection: unhealthy providers
must be auto-downgraded. is_available() = breaker allows AND recent failure rate
acceptable. reliability() feeds the selector's score so a flaky provider loses
traffic gradually before it's cut entirely.
"""

from __future__ import annotations

from collections import defaultdict, deque

from atlas.intelligence.governance.circuit_breaker import CircuitBreaker


class HealthMonitor:
    def __init__(self, window: int = 50) -> None:
        self._results: dict[str, deque[bool]] = defaultdict(lambda: deque(maxlen=window))
        self._latency: dict[str, deque[int]] = defaultdict(lambda: deque(maxlen=window))
        self._breakers: dict[str, CircuitBreaker] = defaultdict(CircuitBreaker)

    def record(self, provider: str, *, ok: bool, latency_ms: int) -> None:
        self._results[provider].append(ok)
        self._latency[provider].append(latency_ms)
        if ok:
            self._breakers[provider].record_success()
        else:
            self._breakers[provider].record_failure()

    def is_available(self, provider: str) -> bool:
        return self._breakers[provider].allow()

    def reliability(self, provider: str) -> float:
        r = self._results[provider]
        return sum(r) / len(r) if r else 1.0

    def snapshot(self) -> dict[str, dict[str, float]]:
        out: dict[str, dict[str, float]] = {}
        for p in self._results:
            lat = self._latency[p]
            out[p] = {
                "reliability": self.reliability(p),
                "avg_latency_ms": (sum(lat) / len(lat)) if lat else 0.0,
                "breaker": 0.0 if self._breakers[p].allow() else 1.0,
            }
        return out
