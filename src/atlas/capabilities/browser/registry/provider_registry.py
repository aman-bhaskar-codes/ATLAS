from __future__ import annotations

import logging

from atlas.capabilities.browser.providers.base import BrowserProvider

_log = logging.getLogger("atlas.browser.registry.provider")

class ProviderRegistry:
    """Manages the available BrowserProvider implementations, ranked by preference."""
    def __init__(self) -> None:
        self._providers: dict[str, BrowserProvider] = {}
        self._preferences: dict[str, int] = {}

    def register(self, provider: BrowserProvider, preference: int = 10) -> None:
        self._providers[provider.name] = provider
        self._preferences[provider.name] = preference
        _log.info(f"Registered BrowserProvider: {provider.name} (pref {preference})")

    def get(self, name: str) -> BrowserProvider | None:
        return self._providers.get(name)

    def best_available(self, required_capabilities: set[str] | None = None) -> BrowserProvider | None:
        """Return the highest preference provider that has the required capabilities."""
        candidates = list(self._providers.values())
        if required_capabilities:
            candidates = [p for p in candidates if self._has_caps(p, required_capabilities)]
        if not candidates:
            return None
        candidates.sort(key=lambda p: self._preferences.get(p.name, 0), reverse=True)
        return candidates[0]

    def _has_caps(self, provider: BrowserProvider, required: set[str]) -> bool:
        caps_dict = provider.capabilities.model_dump()
        return all(caps_dict.get(c) for c in required)

    def all(self) -> list[BrowserProvider]:
        return list(self._providers.values())
