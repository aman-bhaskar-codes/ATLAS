"""Frankfurter currency provider. No auth. https://frankfurter.dev"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import httpx

from atlas.capabilities.domain.common import Provenance, SourceKind
from atlas.capabilities.domain.currency import ExchangeRate
from atlas.capabilities.providers.base import CapabilityRequest, RetryPolicy
from atlas.capabilities.registry.capability import Capability

_BASE_URL = "https://api.frankfurter.dev/v1/latest"


class FrankfurterProvider:
    name = "frankfurter"
    capability = Capability.CURRENCY
    is_local = False
    requires_auth = False

    def __init__(self, timeout_s: float = 15.0) -> None:
        self._client = httpx.AsyncClient(timeout=timeout_s, follow_redirects=True)

    async def initialize(self) -> None: ...
    async def authenticate(self) -> None: ...

    async def health(self) -> bool:
        try:
            r = await self._client.get(_BASE_URL, params={"base": "USD", "symbols": "EUR"})
            return r.status_code == 200
        except httpx.HTTPError:
            return False

    async def convert(self, *, base: str, target: str) -> ExchangeRate | None:
        try:
            r = await self._client.get(_BASE_URL, params={"base": base.upper(), "symbols": target.upper()})
            r.raise_for_status()
            data = r.json()
            rate = data.get("rates", {}).get(target.upper())
            if rate is None:
                return None
            return ExchangeRate(
                base=base.upper(),
                target=target.upper(),
                rate=float(rate),
                date=data.get("date", ""),
                provenance=Provenance(
                    provider=self.name,
                    source_kind=SourceKind.OFFICIAL,
                    uri=str(r.url),
                    retrieved_ts=datetime.now(UTC),
                ),
            )
        except (httpx.HTTPError, KeyError, ValueError, Exception):
            return None

    async def execute(self, request: CapabilityRequest) -> Any:
        return await self.convert(
            base=str(request.args.get("base", "USD")), target=str(request.args.get("target", "EUR"))
        )

    def normalize(self, raw: Any) -> Any:
        return raw

    def retry_policy(self) -> RetryPolicy:
        return RetryPolicy(max_attempts=2, base_backoff_s=0.5)

    async def shutdown(self) -> None:
        await self._client.aclose()