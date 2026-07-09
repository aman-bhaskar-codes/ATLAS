"""Capability registry — the vocabulary of what ATLAS can do externally.

WHY an enum + spec (not free strings): a capability is a first-class, versioned,
permissioned concept. The spec carries the manifest tool-name + operations it maps
to (so the Safety Engine can tier it) and the permission tier hint. Adding a
capability = add an enum member + register a spec. The router and provider registry
key off this.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel

from atlas.capabilities.errors import CapabilityNotFound
from atlas.infra.types import Tier


class Capability(StrEnum):
    KNOWLEDGE = "knowledge"
    BROWSER = "browser"
    EMAIL = "email"
    CALENDAR = "calendar"
    CONTACTS = "contacts"
    NOTIFICATION = "notification"
    CLOUD_STORAGE = "cloud_storage"
    GITHUB = "github"
    WEATHER = "weather"
    LOCATION = "location"
    FILES = "files"
    # extend freely; each needs a CapabilitySpec + at least one provider


class CapabilitySpec(BaseModel):
    """Declared metadata for one capability."""
    model_config = {"frozen": True}
    capability: Capability
    version: int = 1
    description: str = ""
    # the manifest (tool, operation) this maps to, so L1 can classify it.
    safety_tool: str                      # e.g. 'email'
    operations: tuple[str, ...] = ()       # e.g. ('read','search','send')
    default_tier: Tier = Tier.NOTIFY       # baseline; classifier still authoritative
    requires_auth: bool = False
    dependencies: tuple[Capability, ...] = ()


class CapabilityRegistry:
    def __init__(self) -> None:
        self._specs: dict[Capability, CapabilitySpec] = {}

    def register(self, spec: CapabilitySpec) -> None:
        self._specs[spec.capability] = spec

    def get(self, capability: Capability) -> CapabilitySpec:
        spec = self._specs.get(capability)
        if spec is None:
            raise CapabilityNotFound(f"capability {capability.value!r} not registered")
        return spec

    def all(self) -> list[CapabilitySpec]:
        return list(self._specs.values())

    def registered_tools(self) -> dict[str, list[str]]:
        """For doctor --verify-manifest: capability specs -> (tool, operations)."""
        out: dict[str, list[str]] = {}
        for spec in self._specs.values():
            out.setdefault(spec.safety_tool, [])
            out[spec.safety_tool].extend(o for o in spec.operations if o not in out[spec.safety_tool])
        return out
