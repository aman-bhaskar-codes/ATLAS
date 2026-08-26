"""Location domain models: geocoding results + country metadata."""

from __future__ import annotations

from pydantic import BaseModel

from atlas.capabilities.domain.common import Provenance


class GeocodeResult(BaseModel):
    model_config = {"frozen": True}
    query: str
    display_name: str
    latitude: float
    longitude: float
    country: str | None = None
    provenance: Provenance


class CountryInfo(BaseModel):
    model_config = {"frozen": True}
    name: str
    capital: str | None = None
    region: str | None = None
    currencies: tuple[str, ...] = ()
    languages: tuple[str, ...] = ()
    timezones: tuple[str, ...] = ()
    provenance: Provenance
