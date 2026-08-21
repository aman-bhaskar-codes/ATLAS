"""Connector validation — evidence before execution rights.

Validation probes the API (one bounded GET), checks transport safety
(HTTPS-only by default) and records the outcome. Only a passed validation
can promote a connector to VALIDATED. The fetcher is injectable so tests
never touch the network.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from .catalog import CatalogEntry


@dataclass(frozen=True)
class FetchResponse:
    status: int
    body: str
    content_type: str = ""


class HttpFetcher(Protocol):
    async def get(self, url: str, *, timeout_s: float = 10.0) -> FetchResponse: ...


class HttpxFetcher:
    """Production fetcher. Lazy import keeps httpx optional in tests."""

    async def get(self, url: str, *, timeout_s: float = 10.0) -> FetchResponse:
        import httpx

        async with httpx.AsyncClient(follow_redirects=True, timeout=timeout_s) as client:
            resp = await client.get(url, headers={"User-Agent": "ATLAS/1.0 (+connector-validation)"})
            return FetchResponse(
                status=resp.status_code, body=resp.text[:200_000], content_type=resp.headers.get("content-type", "")
            )


@dataclass(frozen=True)
class ValidationResult:
    ok: bool
    note: str
    status_code: int | None = None


class ConnectorValidator:
    def __init__(self, fetcher: HttpFetcher, *, allow_insecure: bool = False) -> None:
        self._fetcher = fetcher
        self._allow_insecure = allow_insecure

    async def validate(self, entry: CatalogEntry, *, probe_path: str = "") -> ValidationResult:
        if not entry.https and not self._allow_insecure:
            return ValidationResult(False, "refused: non-HTTPS API (transport safety policy)")
        if entry.needs_key:
            # Keyed APIs cannot be probed anonymously; validation requires a
            # stored credential + a user-approved probe. Honest refusal here.
            return ValidationResult(
                False, f"requires credential ({entry.auth}); credential-backed validation not yet approved"
            )
        url = entry.url.rstrip("/") + (probe_path or "")
        try:
            resp = await self._fetcher.get(url, timeout_s=10.0)
        except Exception as exc:
            return ValidationResult(False, f"probe failed: {exc}")
        # <500 proves the endpoint is alive and answering; many APIs 4xx without params.
        if resp.status < 500:
            note = f"probe {url} → HTTP {resp.status}; content-type={resp.content_type or 'unknown'}"
            return ValidationResult(True, note, status_code=resp.status)
        return ValidationResult(False, f"probe {url} → HTTP {resp.status}", status_code=resp.status)
