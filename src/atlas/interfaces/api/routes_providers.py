"""Provider, cost, and profile API routes for the zero-cost-first architecture.

These endpoints power the frontend Providers/Cost/Capabilities dashboards
and the CLI commands (atlas providers, atlas cost, atlas profile).
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Request

router = APIRouter(prefix="/api/v1", tags=["providers"])


@router.get("/providers/health")
async def providers_health(request: Request) -> list[dict[str, Any]]:
    """Provider health + quota status for all registered providers."""
    atlas = request.app.state.atlas
    registry = atlas.gateway._runtime._providers
    health = atlas.gateway._runtime._health
    quota = getattr(atlas.gateway._runtime, "_quota", None)
    observations = health.snapshot()
    models = getattr(atlas, "model_registry", None)

    result = []
    for name in registry.names():
        provider = registry.get(name)
        ready = False
        try:
            if provider is not None:
                async with asyncio.timeout(5):
                    ready = bool(await provider.health())
        except Exception:
            # Vendor exceptions can include URLs and credentials. Report the
            # failed probe without returning their raw messages to the browser.
            ready = False
        cost_classes = sorted({m.cost_class.value for m in models.all() if m.provider == name}) if models else []
        entry: dict[str, Any] = {
            "name": name,
            "healthy": ready and health.is_available(name),
            "avg_latency_ms": int(observations.get(name, {}).get("avg_latency_ms", 0)),
            "is_local": getattr(provider, "is_local", False),
            "cost_classes": cost_classes,
        }
        if quota:
            q = quota.remaining(name)
            entry["quota_pct"] = q.get("pct_remaining", 100.0)
            entry["quota_requests_remaining"] = q.get("requests_remaining", -1)
            entry["quota_tokens_remaining"] = q.get("tokens_remaining", -1)
        result.append(entry)
    return result


@router.get("/providers/free")
async def providers_free(request: Request) -> list[dict[str, Any]]:
    """Only free and local providers."""
    all_providers = await providers_health(request)
    return [p for p in all_providers if p.get("is_local") or {"free", "free_quota"}.intersection(p["cost_classes"])]


@router.get("/profile")
async def get_profile(request: Request) -> dict[str, Any]:
    """Current operating profile."""
    atlas = request.app.state.atlas
    from atlas.infra.profiles import resolve_profile

    settings = atlas.settings
    profile = resolve_profile(getattr(settings, "profile", "local_free"))
    return {
        "profile": profile.profile.value,
        "cost_policy": profile.cost_policy.value,
        "network_policy": profile.network_policy.value,
        "allow_cloud": profile.allow_cloud,
        "enable_quota_governor": profile.enable_quota_governor,
        "daily_usd": profile.daily_usd,
        "allowed_cost_classes": sorted(profile.allowed_cost_classes),
    }


@router.get("/providers/quota")
async def providers_quota(request: Request) -> dict[str, Any]:
    """Full quota snapshot for all tracked providers."""
    atlas = request.app.state.atlas
    quota = getattr(atlas.gateway._runtime, "_quota", None)
    if quota is None:
        return {"enabled": False, "providers": {}}
    return {"enabled": True, "providers": quota.snapshot()}


@router.get("/capabilities/matrix")
async def capabilities_matrix(request: Request) -> dict[str, Any]:
    """Capability × provider matrix for the frontend dashboard."""
    from pathlib import Path

    import yaml

    config_path = Path(__file__).resolve().parents[4] / "config" / "models.yaml"
    raw = yaml.safe_load(config_path.read_text()) if config_path.exists() else {}
    models = raw.get("models", [])

    # Build matrix: capability → {local, free_quota, paid}
    matrix: dict[str, dict[str, list[str]]] = {}
    for m in models:
        for cap in m.get("capabilities", []):
            if cap not in matrix:
                matrix[cap] = {"local": [], "free_quota": [], "paid": []}
            cc = m.get("cost_class", "paid")
            if cc in matrix[cap]:
                matrix[cap][cc].append(m.get("id", "?"))
            elif cc == "free":
                matrix[cap]["free_quota"].append(m.get("id", "?"))

    return {"matrix": matrix, "total_models": len(models)}
