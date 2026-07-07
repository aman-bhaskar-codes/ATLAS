"""AXPerceptionTool — perception as a first-class, audited Tool.

WHY a Tool and not a bare function: reading the screen is a capability that must
be legible (audited) and go through L1 like everything else. It is Tier-0 (pure
local read), but it is still classified and logged.
"""

from __future__ import annotations

from typing import Any

from atlas.infra.types import SideEffect, ToolResult
from atlas.perception.backend import PerceptionBackend


class AXPerceptionTool:
    name = "perception"

    def __init__(self, backend: PerceptionBackend) -> None:
        self._backend = backend

    def dry_run(self, args: dict[str, Any]) -> str:
        return "read the frontmost window's accessibility tree (local, read-only)"

    async def execute(self, args: dict[str, Any]) -> ToolResult:
        if not self._backend.available():
            return ToolResult(ok=False, error="perception backend unavailable on this platform")
        state = self._backend.capture_frontmost()
        return ToolResult(
            ok=True,
            output=state.model_dump(),
            side_effects=(SideEffect(kind="screen_read", target=state.app_name or "unknown",
                                     detail=state.source.value, reversible=True),),
        )
