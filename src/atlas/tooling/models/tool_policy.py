"""Tool policy metadata — what the tool DECLARES about itself.

WHY descriptive only: this metadata feeds routing, filtering, and UI (Part 2+).
It never decides whether an invocation is allowed — the SafetyEngine and its
permissions manifest remain the sole authority (Part 1 §12/§26). Keeping the
descriptor separate from the engine makes that boundary explicit: a tool can
claim ``default_tier=AUTO`` and still be classified CONFIRM by the manifest;
the claim is then wrong, the manifest is right, and nothing bypassed it.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from atlas.infra.types import CostClass, PrivacyClass, Tier


class TrustLevel:
    """Trust vocabulary (str constants so pydantic serializes as plain strings).

    SYSTEM_BUILTIN    — shipped with ATLAS, audited in-repo
    USER_CONFIGURED   — explicitly added by the owner (config file)
    LOCAL_MCP         — MCP server launched locally by ATLAS
    TRUSTED_REMOTE    — remote endpoint the owner marked trusted
    THIRD_PARTY_REMOTE— remote endpoint owned by someone else
    UNKNOWN           — unassessed; routers must treat conservatively
    """

    SYSTEM_BUILTIN = "system_builtin"
    USER_CONFIGURED = "user_configured"
    LOCAL_MCP = "local_mcp"
    TRUSTED_REMOTE = "trusted_remote"
    THIRD_PARTY_REMOTE = "third_party_remote"
    UNKNOWN = "unknown"


class ToolPolicyMetadata(BaseModel):
    """Canonical policy-facing descriptor for one tool."""

    model_config = ConfigDict(frozen=True)

    # Safety descriptor — the SafetyEngine manifest remains authoritative.
    default_tier: Tier = Tier.AUTO
    side_effects: bool = False
    supports_rollback: bool = False
    idempotent: bool = True

    # Auth descriptor — credential REFERENCES only, never secret values (§61).
    requires_auth: bool = False

    # Data + network descriptors.
    #: highest data classification this tool may process (LOCAL tools may
    #: process SECRET; anything that ships data off-machine is capped lower)
    max_privacy: PrivacyClass = PrivacyClass.PUBLIC
    network_required: bool = False

    # Cost descriptor — zero-cost-first vocabulary reused from infra.types.
    cost_class: CostClass = CostClass.FREE

    # Trust descriptor (TrustLevel constants above).
    trust_level: str = TrustLevel.SYSTEM_BUILTIN
