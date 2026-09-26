"""Universal tooling fabric — the abstraction layer above every tool source.

``atlas.tools`` stays the home of low-level executable primitives; this package
owns the universal contracts (definition, invocation, result), the registry,
the adapters that bridge native tools and capabilities, and the execution
facade. Everything executes through the existing governed paths (ToolDispatcher
/ CapabilityDispatcher -> SafetyEngine); nothing here bypasses the funnel.
"""

from __future__ import annotations

from atlas.tooling.adapters.base import ToolAdapter
from atlas.tooling.adapters.capability import CapabilityAdapter, capability_definition
from atlas.tooling.adapters.native import NativeToolAdapter, native_definition
from atlas.tooling.compat import to_tool_call_spec, to_tool_call_specs
from atlas.tooling.errors import (
    ToolAdapterError,
    ToolAlreadyRegistered,
    ToolDisabled,
    ToolExecutionError,
    ToolingError,
    ToolInitializationError,
    ToolNotFound,
    ToolRegistrationError,
    ToolValidationError,
)
from atlas.tooling.execution.executor import ToolingExecutor
from atlas.tooling.fabric import InitializationReport, ToolingFabric
from atlas.tooling.registry.registry import ToolingRegistry, ToolRegistration

__all__ = [
    "CapabilityAdapter",
    "InitializationReport",
    "NativeToolAdapter",
    "ToolAdapter",
    "ToolAdapterError",
    "ToolAlreadyRegistered",
    "ToolDisabled",
    "ToolExecutionError",
    "ToolInitializationError",
    "ToolNotFound",
    "ToolRegistration",
    "ToolRegistrationError",
    "ToolValidationError",
    "ToolingError",
    "ToolingExecutor",
    "ToolingFabric",
    "ToolingRegistry",
    "capability_definition",
    "native_definition",
    "to_tool_call_spec",
    "to_tool_call_specs",
]
