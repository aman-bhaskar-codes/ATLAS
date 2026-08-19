"""Reconciles ModelRegistry's OpenRouter entries against live free-model
discovery. WHY here (not in openrouter_free.py): that module is pure discovery
(no side effects, per its own docstring's rule). This module is the one place
allowed to mutate the registry from discovery results, and only for entries
whose provider == 'openrouter' — it never touches local/other-provider specs.
"""

from __future__ import annotations

from atlas.infra.logging import get_logger
from atlas.intelligence.capabilities import Capability
from atlas.intelligence.contracts import ModelSpec
from atlas.intelligence.providers.openrouter_free import discover_free_models
from atlas.intelligence.registry.model_registry import ModelRegistry

_log = get_logger("atlas.intel.openrouter_sync")

_DYNAMIC_PREFIX = "openrouter-dynamic-"


def _to_spec(info) -> ModelSpec:  # type: ignore[no-untyped-def]
    caps: set[Capability] = {Capability.SUMMARIZATION, Capability.CLASSIFICATION}
    if info.supports_tool_calling:
        caps.add(Capability.TOOL_CALLING)
    if info.supports_vision:
        caps.add(Capability.VISION)
    return ModelSpec(
        id=f"{_DYNAMIC_PREFIX}{info.id.replace('/', '-').replace(':', '-')}",
        provider="openrouter",
        provider_model=info.id,
        context_length=info.context_length,
        usd_per_1m_input=0.0,
        usd_per_1m_output=0.0,
        cost_class="free_quota",  # type: ignore[arg-type]
        capabilities=frozenset(caps),
        supports_tool_calling=info.supports_tool_calling,
        supports_vision=info.supports_vision,
        quality_score=0.7,  # neutral prior for auto-discovered models — not hand-curated
        reliability_score=0.8,  # conservative: unverified beyond "currently listed as free"
        enabled=True,
    )


async def sync_openrouter_free_models(registry: ModelRegistry) -> int:
    """Discover currently-free OpenRouter models and register/refresh them.
    Never raises: a failed discovery just means no update this cycle — the
    static models.yaml entries (if any) remain the fallback, exactly per
    openrouter_free.py's own fail-closed contract.

    Returns the number of models synced (0 on discovery failure).
    """
    discovery = await discover_free_models()
    if not discovery.ok:
        _log.warning("openrouter_sync.discovery_failed", event_type="lifecycle", error=discovery.error)
        return 0

    # Disable previously-synced dynamic entries not present in this run
    # (they were delisted) before adding the current set.
    current_ids = {f"{_DYNAMIC_PREFIX}{m.id.replace('/', '-').replace(':', '-')}" for m in discovery.models}
    for spec in registry.all(include_disabled=True):
        if spec.id.startswith(_DYNAMIC_PREFIX) and spec.id not in current_ids:
            registry.disable(spec.id)
            _log.info("openrouter_sync.delisted", event_type="lifecycle", model_id=spec.id)

    for info in discovery.models:
        registry.register(_to_spec(info))

    _log.info("openrouter_sync.synced", event_type="lifecycle", count=len(discovery.models))
    return len(discovery.models)
