"""Capability index — capability -> candidate models. WHY: selection starts from
'who can do this?', which is a set-containment query the index answers fast."""

from __future__ import annotations

from atlas.intelligence.capabilities import CapabilitySet
from atlas.intelligence.contracts import ModelSpec
from atlas.intelligence.registry.model_registry import ModelRegistry


class CapabilityIndex:
    def __init__(self, registry: ModelRegistry) -> None:
        self._registry = registry

    def candidates(self, required: CapabilitySet) -> list[ModelSpec]:
        return [m for m in self._registry.all() if required.issubset(m.capabilities)]
