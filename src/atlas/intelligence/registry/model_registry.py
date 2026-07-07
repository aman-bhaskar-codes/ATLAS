"""Model registry — config-driven metadata, zero hardcoding.

WHY config-driven: onboarding a model = a YAML edit. The registry validates and
indexes ModelSpecs; reliability_score is later updated live by the health
monitor (so the registry is the single truth for both static + dynamic model
metadata).
"""

from __future__ import annotations

from pathlib import Path

import yaml

from atlas.intelligence.capabilities import parse_capabilities
from atlas.intelligence.contracts import ModelSpec
from atlas.intelligence.errors import ConfigurationError


class ModelRegistry:
    def __init__(self, specs: dict[str, ModelSpec]) -> None:
        self._specs = specs

    @classmethod
    def from_yaml(cls, path: Path) -> ModelRegistry:
        raw = yaml.safe_load(path.read_text()) if path.exists() else {}
        specs: dict[str, ModelSpec] = {}
        for entry in (raw or {}).get("models", []):
            try:
                caps = parse_capabilities(entry.get("capabilities", []))
                spec = ModelSpec(**{**entry, "capabilities": caps})
            except Exception as exc:
                raise ConfigurationError(f"bad model spec {entry.get('id')}: {exc}") from exc
            specs[spec.id] = spec
        if not specs:
            raise ConfigurationError("no models configured")
        return cls(specs)

    def get(self, model_id: str) -> ModelSpec | None:
        return self._specs.get(model_id)

    def all(self) -> list[ModelSpec]:
        return [s for s in self._specs.values() if s.enabled]

    def update_reliability(self, model_id: str, score: float) -> None:
        spec = self._specs.get(model_id)
        if spec is not None:
            self._specs[model_id] = spec.model_copy(update={"reliability_score": score})
