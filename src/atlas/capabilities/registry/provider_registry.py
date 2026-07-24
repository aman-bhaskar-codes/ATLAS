"""Provider registry — capability -> ranked healthy providers.

WHY ranking here mirrors Phase-5 selection: the ordered list of providers IS the
fallback chain the dispatcher walks. Ranking prefers local/official + high health
(free-first, reliable-first). Unhealthy providers (open breaker) are filtered out
so traffic auto-reroutes — no cascading failure.
"""

from __future__ import annotations

from atlas.capabilities.errors import NoProviderAvailable
from atlas.capabilities.providers.base import Provider
from atlas.capabilities.registry.capability import Capability
from atlas.capabilities.registry.health import CapabilityHealth


class ProviderRegistry:
    def __init__(self, health: CapabilityHealth) -> None:
        self._by_capability: dict[Capability, list[Provider]] = {}
        self._health = health
        self._prefs: dict[tuple[Capability, str], int] = {}

    def register(self, provider: Provider, *, preference: int = 100) -> None:
        """preference: lower = tried first among equally-healthy providers
        (use it to prefer free/official/local adapters)."""
        if provider.name in {p.name for p in self._by_capability.get(provider.capability, [])}:
            raise ValueError(f"duplicate provider name {provider.name!r} for {provider.capability.value}")
        self._by_capability.setdefault(provider.capability, [])
        self._by_capability[provider.capability].append(provider)
        self._prefs[(provider.capability, provider.name)] = preference

    def candidates(self, capability: Capability) -> list[Provider]:
        providers = self._by_capability.get(capability, [])
        healthy = [p for p in providers if self._health.is_available(p.name)]
        if not healthy:
            raise NoProviderAvailable(
                f"no healthy provider for capability {capability.value!r}")
        return sorted(
            healthy,
            key=lambda p: (self._prefs.get((capability, p.name), 100),
                           -self._health.reliability(p.name)),
        )

    def all_providers(self) -> list[Provider]:
        return [p for ps in self._by_capability.values() for p in ps]

