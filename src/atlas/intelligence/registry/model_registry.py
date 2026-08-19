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

    def all(self, include_disabled: bool = False) -> list[ModelSpec]:
        return [s for s in self._specs.values() if include_disabled or s.enabled]

    def register(self, spec: ModelSpec) -> None:
        """Runtime registration — used by the OpenRouter free-model sync task
        to add/refresh specs discovered live, without editing models.yaml."""
        self._specs[spec.id] = spec

    def disable(self, model_id: str) -> None:
        """Mark a spec unavailable without deleting it — used when a free
        model gets delisted, so it can reappear cleanly if OpenRouter adds it back."""
        spec = self._specs.get(model_id)
        if spec is not None:
            self._specs[model_id] = spec.model_copy(update={"enabled": False})

    def update_reliability(self, model_id: str, score: float) -> None:
        spec = self._specs.get(model_id)
        if spec is not None:
            self._specs[model_id] = spec.model_copy(update={"reliability_score": score})
