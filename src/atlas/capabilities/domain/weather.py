"""Weather domain model. WHY separate from knowledge.py: weather has its own
shape (numeric forecast fields), not a text/snippet retrieval result."""

from __future__ import annotations

from pydantic import BaseModel

from atlas.capabilities.domain.common import Provenance


class DailyForecast(BaseModel):
    model_config = {"frozen": True}
    date: str
    temp_min_c: float
    temp_max_c: float
    precipitation_mm: float
    weather_code: int


class WeatherReport(BaseModel):
    model_config = {"frozen": True}
    latitude: float
    longitude: float
    timezone: str
    current_temp_c: float | None = None
    current_weather_code: int | None = None
    daily: tuple[DailyForecast, ...] = ()
    provenance: Provenance