"""Weather Platform — get_forecast(): the one call the orchestrator makes.
Read-only, no side effects, so no Safety Engine gate needed (same reasoning
as CalendarPlatform's list_events/search/free_busy reads)."""

from __future__ import annotations

from atlas.capabilities.domain.weather import WeatherReport
from atlas.capabilities.providers.weather.base import WeatherProvider


class WeatherPlatform:
    def __init__(self, *, provider: WeatherProvider) -> None:
        self._provider = provider

    async def get_forecast(self, *, latitude: float, longitude: float, days: int = 3) -> WeatherReport:
        return await self._provider.forecast(latitude=latitude, longitude=longitude, days=days)