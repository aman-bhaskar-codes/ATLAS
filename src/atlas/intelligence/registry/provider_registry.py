"""Provider registry — provider name -> live adapter. WHY: the selector picks a
ModelSpec; the runtime needs the adapter for spec.provider. One lookup table,
populated at wiring based on which API keys exist + allow_cloud."""

from __future__ import annotations

from atlas.intelligence.providers.base import Provider


class ProviderRegistry:
    def __init__(self) -> None:
        self._providers: dict[str, Provider] = {}

    def register(self, provider: Provider) -> None:
        self._providers[provider.name] = provider

    def get(self, name: str) -> Provider | None:
        return self._providers.get(name)

    def names(self) -> list[str]:
        return list(self._providers)

    async def close(self) -> None:
        for p in self._providers.values():
            await p.close()
