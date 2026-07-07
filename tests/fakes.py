"""Test doubles."""

from __future__ import annotations

from typing import Any

from atlas.infra.types import SideEffect, ToolResult


class FakeClock:
    def __init__(self, now: Any) -> None:
        self._now = now
    def now(self) -> Any:
        return self._now


class FakeIdGen:
    def task_id(self) -> str: return "tid-1"
    def correlation_id(self) -> str: return "cid-1"
    def execution_id(self) -> str: return "eid-1"





class FakeTool:
    name = "fake.tool"
    def __init__(self, ok: bool = True, output: Any = "did it") -> None:
        self._ok = ok
        self._output = output
        self.calls: list[dict[str, Any]] = []

    def dry_run(self, args: dict[str, Any]) -> str:
        return f"would fake {args}"

    async def execute(self, args: dict[str, Any]) -> ToolResult:
        self.calls.append(args)
        return ToolResult(
            ok=self._ok, output=self._output,
            side_effects=(SideEffect(kind="fake", target="none"),),
        )


class FakeConfirmer:
    def __init__(self, response: bool) -> None:
        self._response = response
        self.calls: list[str] = []

    async def confirm(self, prompt: str, decision: Any, req: Any) -> bool:
        self.calls.append(prompt)
        return self._response


class FakeKillSwitch:
    def __init__(self, active: bool = False) -> None:
        self._active = active
    def is_active(self) -> bool:
        return self._active
