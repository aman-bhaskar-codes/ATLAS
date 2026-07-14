from __future__ import annotations

from atlas.capabilities.registry.tier import Tier

from atlas.capabilities.registry.capability import Capability, CapabilitySpec

BROWSER_CAPABILITY = CapabilitySpec(
    capability=Capability.BROWSER,
    safety_tool="browser",
    operations=(
        "navigate", "extract", "screenshot", "click", "type",
        "submit", "upload", "download", "dialog"
    ),
    default_tier=Tier.NOTIFY,
    requires_auth=False,
    description="Provider-agnostic web browser automation. All mutations are previewed."
)
