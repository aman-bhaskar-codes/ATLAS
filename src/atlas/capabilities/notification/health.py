"""Provider Health.

Tracks provider availability.
"""

from __future__ import annotations


class ProviderHealth:
    def __init__(self) -> None:
        self._failures: dict[str, int] = {}
        
    def record(self, provider: str, *, ok: bool, latency_ms: int) -> None:
        if ok:
            self._failures[provider] = 0
        else:
            self._failures[provider] = self._failures.get(provider, 0) + 1

    def is_available(self, provider: str) -> bool:
        # Breaker trips after 3 consecutive failures
        return self._failures.get(provider, 0) < 3
