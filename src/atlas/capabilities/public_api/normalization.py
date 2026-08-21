"""Response normalization — external API output becomes bounded, provenanced,
UNTRUSTED data.

Security boundary (Phase 48): connector responses are DATA, never instructions.
Normalization truncates, depth-limits and tags the payload with provenance so
downstream reasoning can cite sources and never mistakes fetched text for
commands to obey.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel

from atlas.capabilities.domain.common import Provenance, SourceKind

_MAX_DEPTH = 6
_MAX_LIST_ITEMS = 25
_MAX_STR_LEN = 2000


class NormalizedAPIResult(BaseModel):
    """A normalized, provenance-tagged external API response."""

    model_config = {"frozen": True}
    ok: bool
    payload: Any = None
    provenance: Provenance
    status_code: int | None = None
    trust: str = "untrusted"  # external data is untrusted by construction
    error: str | None = None


def _sanitize(value: Any, depth: int = 0) -> Any:
    if depth > _MAX_DEPTH:
        return "...(truncated depth)"
    if isinstance(value, dict):
        return {str(k): _sanitize(v, depth + 1) for k, v in list(value.items())[:50]}
    if isinstance(value, list):
        trimmed = [_sanitize(v, depth + 1) for v in value[:_MAX_LIST_ITEMS]]
        if len(value) > _MAX_LIST_ITEMS:
            trimmed.append(f"...(+{len(value) - _MAX_LIST_ITEMS} more items)")
        return trimmed
    if isinstance(value, str) and len(value) > _MAX_STR_LEN:
        return value[:_MAX_STR_LEN] + "...(truncated)"
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)[:_MAX_STR_LEN]


def normalize_response(
    body: str,
    *,
    provider: str,
    url: str,
    status_code: int,
    content_type: str = "",
) -> NormalizedAPIResult:
    """Convert a raw HTTP body into a bounded, provenanced result."""
    provenance = Provenance(
        provider=provider,
        source_kind=SourceKind.WEB,
        uri=url,
        retrieved_ts=datetime.now(UTC),
    )
    if status_code >= 400:
        return NormalizedAPIResult(
            ok=False, provenance=provenance, status_code=status_code, error=f"HTTP {status_code}"
        )
    payload: Any
    if "json" in content_type or body.lstrip().startswith(("{", "[")):
        try:
            payload = _sanitize(json.loads(body))
        except json.JSONDecodeError:
            payload = _sanitize(body)
    else:
        payload = _sanitize(body)
    return NormalizedAPIResult(ok=True, payload=payload, provenance=provenance, status_code=status_code)
