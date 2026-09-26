"""Tooling adapters — translation between the universal layer and backends."""

from __future__ import annotations

from atlas.tooling.adapters.base import ToolAdapter
from atlas.tooling.adapters.capability import CapabilityAdapter, capability_definition
from atlas.tooling.adapters.native import NativeToolAdapter, native_definition

__all__ = [
    "CapabilityAdapter",
    "NativeToolAdapter",
    "ToolAdapter",
    "capability_definition",
    "native_definition",
]
