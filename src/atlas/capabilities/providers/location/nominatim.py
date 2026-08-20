"""Nominatim (OpenStreetMap) geocoding + REST Countries metadata provider.
Both are free, no-auth. Nominatim REQUIRES a descriptive User-Agent per its
usage policy: https://operations.osmfoundation.org/policies/nominatim/"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import httpx

from atlas.capabilities.domain.common import Provenance, SourceKind
from atlas.capabilities.domain.location import CountryInfo, GeocodeResult
from atlas.capabilities.providers.base import CapabilityRequest, RetryPolicy
from atlas.capabilities.registry.capability import Capability

_NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
_COUNTRIES_URL = "https://restcountries.com/v3.1/name"


class NominatimProvider:
    name = "nominatim"
    capability = Capability.LOCATION
    is_local = False
    requires_auth = False

    def __init__(
        self,
        timeout_s: float = 15.0,
        user_agent: str = "ATLAS/0.1.0 (github.com/aman-bhaskar-codes/ATLAS)",
    ) -> None:
        self._client = httpx.AsyncClient(
            timeout=timeout_s, follow_redirects=True, headers={"User-Agent": user_agent}
        )

    async def initialize(self) -> None: ...
    async def authenticate(self) -> None: ...

    async def health(self) -> bool:
        try:
            r = await self._client.get(_NOMINATIM_URL, params={"q": "Berlin", "format": "json", "limit": 1})
            return r.status_code == 200
        except httpx.HTTPError:
            return False

    async def geocode(self, query: str) -> GeocodeResult | None:
        try:
            r = await self._client.get(_NOMINATIM_URL, params={"q": query, "format": "json", "limit": 1})
            r.raise_for_status()
            results = r.json()
            if not results:
                return None
            top = results[0]
            address = top.get("address", {})
            return GeocodeResult(
                query=query,
                display_name=top.get("display_name", query),
                latitude=float(top["lat"]),
                longitude=float(top["lon"]),
                country=address.get("country") if isinstance(address, dict) else None,
                provenance=Provenance(
                    provider=self.name,
                    source_kind=SourceKind.OFFICIAL,
                    uri=str(r.url),
                    retrieved_ts=datetime.now(UTC),
                ),
            )
        except (httpx.HTTPError, KeyError, ValueError, IndexError, Exception):
            return None

    async def country_info(self, country: str) -> CountryInfo | None:
        try:
            r = await self._client.get(
                f"{_COUNTRIES_URL}/{country}",
                params={"fields": "name,capital,region,currencies,languages,timezones"},
            )
            r.raise_for_status()
            data = r.json()
            top = data[0] if isinstance(data, list) else data
            currencies = tuple((top.get("currencies") or {}).keys())
            languages = tuple((top.get("languages") or {}).values())
            capital_list = top.get("capital")
            capital = capital_list[0] if isinstance(capital_list, list) and capital_list else None
            return CountryInfo(
                name=top.get("name", {}).get("common", country),
                capital=capital,
                region=top.get("region"),
                currencies=currencies,
                languages=languages,
                timezones=tuple(top.get("timezones", ())),
                provenance=Provenance(
                    provider=self.name,
                    source_kind=SourceKind.OFFICIAL,
                    uri=str(r.url),
                    retrieved_ts=datetime.now(UTC),
                ),
            )
        except (httpx.HTTPError, KeyError, IndexError, Exception):
            return None

    async def execute(self, request: CapabilityRequest) -> Any:
        op = request.operation
        if op == "geocode":
            return await self.geocode(str(request.args.get("query", "")))
        if op == "country_info":
            return await self.country_info(str(request.args.get("country", "")))
        return None

    def normalize(self, raw: Any) -> Any:
        return raw

    def retry_policy(self) -> RetryPolicy:
        return RetryPolicy(max_attempts=2, base_backoff_s=1.0)

    async def shutdown(self) -> None:
        await self._client.aclose()