"""Currency Platform — convert(), read-only, no side effects."""

from __future__ import annotations

from atlas.capabilities.domain.currency import ExchangeRate
from atlas.capabilities.providers.currency.base import CurrencyProvider


class CurrencyPlatform:
    def __init__(self, *, provider: CurrencyProvider) -> None:
        self._provider = provider

    async def convert(self, *, base: str, target: str) -> ExchangeRate | None:
        return await self._provider.convert(base=base, target=target)