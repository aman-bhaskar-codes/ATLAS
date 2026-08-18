"""Operating profiles — runtime configuration presets.

WHY profiles: a single `ATLAS_PROFILE=local_free` env var replaces a dozen
individual settings. Profiles drive which providers are registered, what
policies are active, and what defaults apply. Switching profiles NEVER
requires changing orchestration logic.

Profiles:
  local_free       — Ollama only, offline, $0. Default development mode.
  free_hybrid      — local + free-tier cloud (Groq, Gemini, OpenRouter).
  free_demo        — optimized for public demos: rate limiting, auto-sleep.
  production       — full infrastructure, paid providers optional.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from atlas.infra.types import CostPolicy, NetworkPolicy, PrivacyClass


class AtlasProfile(StrEnum):
    LOCAL_FREE = "local_free"
    FREE_HYBRID = "free_hybrid"
    FREE_DEMO = "free_demo"
    PRODUCTION = "production"


@dataclass(frozen=True)
class ProfileConfig:
    """Resolved configuration for a profile."""

    profile: AtlasProfile
    cost_policy: CostPolicy
    network_policy: NetworkPolicy
    default_privacy: PrivacyClass
    allow_cloud: bool
    enable_quota_governor: bool
    enable_rate_limiting: bool
    # Model registration filters
    allowed_cost_classes: frozenset[str]  # which cost_class values to register
    # Budget defaults (daily USD)
    daily_usd: float
    weekly_usd: float
    monthly_usd: float
    per_task_usd: float


# ── Profile definitions ──────────────────────────────────────────────────

_PROFILES: dict[AtlasProfile, ProfileConfig] = {
    AtlasProfile.LOCAL_FREE: ProfileConfig(
        profile=AtlasProfile.LOCAL_FREE,
        cost_policy=CostPolicy.ZERO_COST,
        network_policy=NetworkPolicy.LOCAL_ONLY,
        default_privacy=PrivacyClass.PRIVATE,
        allow_cloud=False,
        enable_quota_governor=False,
        enable_rate_limiting=False,
        allowed_cost_classes=frozenset({"local"}),
        daily_usd=0.0,
        weekly_usd=0.0,
        monthly_usd=0.0,
        per_task_usd=0.0,
    ),
    AtlasProfile.FREE_HYBRID: ProfileConfig(
        profile=AtlasProfile.FREE_HYBRID,
        cost_policy=CostPolicy.FREE_ONLY,
        network_policy=NetworkPolicy.FREE_CLOUD,
        default_privacy=PrivacyClass.INTERNAL,
        allow_cloud=True,
        enable_quota_governor=True,
        enable_rate_limiting=True,
        allowed_cost_classes=frozenset({"local", "free", "free_quota"}),
        daily_usd=0.0,
        weekly_usd=0.0,
        monthly_usd=0.0,
        per_task_usd=0.0,
    ),
    AtlasProfile.FREE_DEMO: ProfileConfig(
        profile=AtlasProfile.FREE_DEMO,
        cost_policy=CostPolicy.FREE_PREFERRED,
        network_policy=NetworkPolicy.FREE_CLOUD,
        default_privacy=PrivacyClass.PUBLIC,
        allow_cloud=True,
        enable_quota_governor=True,
        enable_rate_limiting=True,
        allowed_cost_classes=frozenset({"local", "free", "free_quota"}),
        daily_usd=0.50,
        weekly_usd=2.00,
        monthly_usd=5.00,
        per_task_usd=0.10,
    ),
    AtlasProfile.PRODUCTION: ProfileConfig(
        profile=AtlasProfile.PRODUCTION,
        cost_policy=CostPolicy.BALANCED,
        network_policy=NetworkPolicy.UNRESTRICTED,
        default_privacy=PrivacyClass.INTERNAL,
        allow_cloud=True,
        enable_quota_governor=True,
        enable_rate_limiting=True,
        allowed_cost_classes=frozenset({"local", "free", "free_quota", "paid"}),
        daily_usd=5.00,
        weekly_usd=20.00,
        monthly_usd=50.00,
        per_task_usd=1.00,
    ),
}


def resolve_profile(name: str) -> ProfileConfig:
    """Resolve a profile name (from settings/env) to its configuration.

    Falls back to LOCAL_FREE if the name is unknown — fail-safe.
    """
    try:
        profile = AtlasProfile(name.lower())
    except ValueError:
        profile = AtlasProfile.LOCAL_FREE
    return _PROFILES[profile]


def list_profiles() -> list[ProfileConfig]:
    """All available profiles — for CLI/dashboard listing."""
    return list(_PROFILES.values())
