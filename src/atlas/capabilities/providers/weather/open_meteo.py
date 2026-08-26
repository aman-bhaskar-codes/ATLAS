"""Open-Meteo weather provider. No API key. source: https://open-meteo.com/"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import httpx

from atlas.capabilities.domain.common import Provenance, SourceKind
from atlas.capabilities.domain.weather import DailyForecast, WeatherReport
from atlas.capabilities.providers.base import CapabilityRequest, RetryPolicy
from atlas.capabilities.registry.capability import Capability

_BASE_URL = "https://api.open-meteo.com/v1/forecast"


class OpenMeteoProvider:
    name = "open_meteo"
    capability = Capability.WEATHER
    is_local = False
    requires_auth = False

    def __init__(self, timeout_s: float = 15.0) -> None:
        self._client = httpx.AsyncClient(timeout=timeout_s, follow_redirects=True)

    async def initialize(self) -> None: ...
    async def authenticate(self) -> None: ...

    async def health(self) -> bool:
        try:
            r = await self._client.get(_BASE_URL, params={"latitude": 0, "longitude": 0, "current": "temperature_2m"})
            return r.status_code == 200
        except httpx.HTTPError:
            return False

    async def forecast(self, *, latitude: float, longitude: float, days: int = 3) -> WeatherReport:
        try:
            r = await self._client.get(
                _BASE_URL,
                params={
                    "latitude": latitude,
                    "longitude": longitude,
                    "current": "temperature_2m,weather_code",
                    "daily": "temperature_2m_min,temperature_2m_max,precipitation_sum,weather_code",
                    "forecast_days": min(max(days, 1), 16),
                    "timezone": "auto",
                },
            )
            r.raise_for_status()
            data = r.json()

            current = data.get("current", {})
            daily = data.get("daily", {})
            dates = daily.get("time", [])
            tmin = daily.get("temperature_2m_min", [])
            tmax = daily.get("temperature_2m_max", [])
            precip = daily.get("precipitation_sum", [])
            codes = daily.get("weather_code", [])

            forecasts = tuple(
                DailyForecast(
                    date=dates[i],
                    temp_min_c=tmin[i],
                    temp_max_c=tmax[i],
                    precipitation_mm=precip[i],
                    weather_code=codes[i],
                )
                for i in range(len(dates))
            )

            return WeatherReport(
                latitude=data.get("latitude", latitude),
                longitude=data.get("longitude", longitude),
                timezone=data.get("timezone", "UTC"),
                current_temp_c=current.get("temperature_2m"),
                current_weather_code=current.get("weather_code"),
                daily=forecasts,
                provenance=Provenance(
                    provider=self.name,
                    source_kind=SourceKind.OFFICIAL,
                    uri=str(r.url),
                    retrieved_ts=datetime.now(UTC),
                ),
            )
        except (httpx.HTTPError, KeyError, IndexError, Exception):
            return WeatherReport(
                latitude=latitude,
                longitude=longitude,
                timezone="UTC",
                provenance=Provenance(
                    provider=self.name,
                    source_kind=SourceKind.OFFICIAL,
                    uri=None,
                    retrieved_ts=datetime.now(UTC),
                ),
            )

    async def execute(self, request: CapabilityRequest) -> Any:
        return await self.forecast(
            latitude=float(request.args.get("latitude", 0.0)),
            longitude=float(request.args.get("longitude", 0.0)),
            days=int(request.args.get("days", 3)),
        )

    def normalize(self, raw: Any) -> Any:
        return raw

    def retry_policy(self) -> RetryPolicy:
        return RetryPolicy(max_attempts=2, base_backoff_s=0.5)

    async def shutdown(self) -> None:
        await self._client.aclose()
