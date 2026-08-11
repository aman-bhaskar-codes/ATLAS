"""Capability vocabulary — the routing currency.

WHY an enum + frozenset: routing is capability-based, never model-based. A
request declares REQUIRED capabilities; a model ADVERTISES a capability set;
selection is set-containment. This decouples 'what the task needs' from 'which
model exists today'.
"""

from __future__ import annotations

from atlas.infra.types import ModelCapability as Capability
from atlas.infra.types import ModelCapabilitySet as CapabilitySet

__all__ = ["Capability", "CapabilitySet", "parse_capabilities"]


def parse_capabilities(values: list[str]) -> CapabilitySet:
    out: set[Capability] = set()
    for v in values:
        try:
            out.add(Capability(v))
        except ValueError as exc:
            raise ValueError(f"unknown capability {v!r}") from exc
    return frozenset(out)
