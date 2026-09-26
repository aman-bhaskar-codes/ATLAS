"""ToolAdapter — the one execution contract every tool source must satisfy.

The adapter translates between the universal layer and one implementation
universe:

    UniversalToolInvocation -> adapter-specific call -> backend
    backend result          -> UniversalToolResult

Adapters TRANSLATE only (Part 1 §53): no routing, no safety policy, no provider
ranking lives here. Execution always reaches the existing governed funnels —
``ToolDispatcher`` for native tools, ``CapabilityDispatcher`` for capabilities —
which enter the SafetyEngine. There is no path invocation -> adapter -> backend
that skips it.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from atlas.tooling.models.tool_definition import UniversalToolDefinition
from atlas.tooling.models.tool_invocation import UniversalToolInvocation
from atlas.tooling.models.tool_result import UniversalToolResult


@runtime_checkable
class ToolAdapter(Protocol):
    """One adapter instance serves every tool of its kind (e.g. a single
    ``NativeToolAdapter`` fronts all native tools). ``kind`` names the adapter
    family and is stored on definitions it registers."""

    kind: str

    async def initialize(self) -> None:
        """Prepare the adapter. MUST be cheap or lazy — no fake readiness."""
        ...

    async def validate(self, definition: UniversalToolDefinition) -> None:
        """Verify this definition is actually executable through the adapter
        (implementation exists, wiring present). Raises ToolValidationError."""
        ...

    async def execute(
        self,
        invocation: UniversalToolInvocation,
        definition: UniversalToolDefinition,
    ) -> UniversalToolResult:
        """Execute one operation through the governed path and normalize the
        result. Execution-domain failures are RESULTS, not exceptions."""
        ...

    async def health(self) -> bool:
        """Cheap liveness probe for the adapter as a whole."""
        ...

    async def shutdown(self) -> None:
        """Release adapter-level resources. Called once per adapter."""
        ...
