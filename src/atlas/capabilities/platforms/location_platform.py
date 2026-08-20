"""Location Platform — geocode() and country_info(), both read-only reads."""

from __future__ import annotations

from atlas.capabilities.domain.location import CountryInfo, GeocodeResult
from atlas.capabilities.providers.location.base import LocationProvider


class LocationPlatform:
    def __init__(self, *, provider: LocationProvider) -> None:
        self._provider = provider

    async def geocode(self, query: str) -> GeocodeResult | None:
        return await self._provider.geocode(query)

    async def country_info(self, country: str) -> CountryInfo | None:
        return await self._provider.country_info(country)