"""OpenRouter free-model discovery.

WHY: OpenRouter's free-model lineup changes over time. Static entries in
models.yaml go stale; this module queries OpenRouter's public /models endpoint
(no key required for listing) and reports which models are currently free
(prompt AND completion pricing zero), with a `last_verified` timestamp.

RULES (free-first contract):
- Discovery NEVER mutates models.yaml or the live registry automatically.
- A model not seen in the latest discovery run must be treated as gone.
- Failure to reach the API returns an EMPTY discovery with verified_at set —
  callers degrade to the static registry rather than assuming availability.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import httpx

from atlas.infra.logging import get_logger

_log = get_logger("atlas.intel.openrouter")

_OPENROUTER_MODELS_URL = "https://openrouter.ai/api/v1/models"
_TIMEOUT_S = 15.0


@dataclass(frozen=True)
class FreeModelInfo:
    """One currently-free OpenRouter model."""

    id: str  # OpenRouter's own id, e.g. 'meta-llama/llama-3.3-70b-instruct:free'
    name: str
    context_length: int
    supports_tool_calling: bool
    supports_vision: bool


@dataclass
class FreeModelDiscovery:
    verified_at: datetime
    models: list[FreeModelInfo] = field(default_factory=list)
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


def _is_free(entry: dict[str, Any]) -> bool:
    pricing = entry.get("pricing") or {}
    try:
        return float(pricing.get("prompt", "1")) == 0.0 and float(pricing.get("completion", "1")) == 0.0
    except (TypeError, ValueError):
        return False


def _parse(entry: dict[str, Any]) -> FreeModelInfo | None:
    try:
        architecture = entry.get("architecture") or {}
        modality = str(architecture.get("input_modalities") or "")
        return FreeModelInfo(
            id=str(entry["id"]),
            name=str(entry.get("name", entry["id"])),
            context_length=int(entry.get("context_length") or 8192),
            supports_tool_calling=bool((entry.get("supported_parameters") or []).count("tools")),
            supports_vision="image" in modality,
        )
    except (KeyError, TypeError, ValueError) as exc:
        _log.warning("openrouter.skip_entry", event_type="intel", error=repr(exc))
        return None


async def discover_free_models(client: httpx.AsyncClient | None = None) -> FreeModelDiscovery:
    """Fetch OpenRouter's current model list and return the free subset.

    Never raises: network/schema failures produce a discovery with `error` set
    and an empty model list, so callers fall back to static config.
    """
    own_client = client is None
    c = client or httpx.AsyncClient(timeout=_TIMEOUT_S)
    try:
        r = await c.get(_OPENROUTER_MODELS_URL)
        r.raise_for_status()
        data = r.json()
        models: list[FreeModelInfo] = []
        for entry in data.get("data", []):
            if _is_free(entry):
                parsed = _parse(entry)
                if parsed is not None:
                    models.append(parsed)
        models.sort(key=lambda m: m.id)
        _log.info("openrouter.discovered", event_type="intel", free_models=len(models))
        return FreeModelDiscovery(verified_at=datetime.now(UTC), models=models)
    except (httpx.HTTPError, json.JSONDecodeError, ValueError) as exc:
        _log.warning("openrouter.discovery_failed", event_type="intel", error=repr(exc))
        return FreeModelDiscovery(verified_at=datetime.now(UTC), error=repr(exc))
    finally:
        if own_client:
            await c.aclose()


def diff_against_static(
    discovery: FreeModelDiscovery, known_provider_model_ids: set[str]
) -> dict[str, list[str]]:
    """Compare a discovery against static models.yaml entries.

    Returns {'appeared': [...], 'disappeared': [...]} of OpenRouter model ids.
    'disappeared' entries must be disabled — a free model is never assumed
    permanent.
    """
    discovered_ids = {m.id for m in discovery.models}
    return {
        "appeared": sorted(discovered_ids - known_provider_model_ids),
        "disappeared": sorted(known_provider_model_ids - discovered_ids),
    }
