"""Tool protocol. WHY here in Phase 1: the Safety Engine funnels `Tool`s, so the
contract must exist even though real tools arrive in Phase 2. `dry_run` returns
a human-readable preview used by the confirmation flow and (later) the eval
harness — it must never cause a side effect."""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from atlas.infra.types import ToolResult


@runtime_checkable
class Tool(Protocol):
    name: str

    def dry_run(self, args: dict[str, Any]) -> str: ...
    async def execute(self, args: dict[str, Any]) -> ToolResult: ...
