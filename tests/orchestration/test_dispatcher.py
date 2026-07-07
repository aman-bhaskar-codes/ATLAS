from typing import Any

from atlas.infra.ids import CorrelationId
from atlas.infra.types import SafetyDecision, Tier, ToolRequest, ToolResult
from atlas.orchestration.dispatcher import ToolDispatcher
from atlas.orchestration.registry import ToolRegistry
from atlas.orchestration.types import Action
from atlas.safety.engine import DeniedError


class FakeTool:
    name = "filesystem"
    def dry_run(self, args: dict[str, Any]) -> str: return "x"
    async def execute(self, args: dict[str, Any]) -> ToolResult:
        return ToolResult(ok=True, output={"ok": True})


class AllowSafety:
    async def guard(self, req: ToolRequest, tool: Any) -> ToolResult:
        result: ToolResult = await tool.execute(req.args)
        return result


class DenySafety:
    async def guard(self, req: ToolRequest, tool: Any) -> ToolResult:
        raise DeniedError(SafetyDecision(decision="deny", tier=Tier.BLOCK, reason="nope"))


async def test_dispatch_ok() -> None:
    reg = ToolRegistry()
    reg.register(FakeTool(), ("read",))
    d = ToolDispatcher(reg, AllowSafety())  # type: ignore[arg-type]
    obs = await d.dispatch(Action(step=1, kind="tool_call", tool="filesystem",
                                  operation="read", args={}), CorrelationId("c"))
    assert obs.ok


async def test_denial_becomes_observation_not_crash() -> None:
    reg = ToolRegistry()
    reg.register(FakeTool(), ("read",))
    d = ToolDispatcher(reg, DenySafety())  # type: ignore[arg-type]
    obs = await d.dispatch(Action(step=1, kind="tool_call", tool="filesystem",
                                  operation="delete", args={}), CorrelationId("c"))
    assert not obs.ok and "denied" in (obs.error or "")


async def test_unknown_tool_is_observation() -> None:
    d = ToolDispatcher(ToolRegistry(), AllowSafety())  # type: ignore[arg-type]
    obs = await d.dispatch(Action(step=1, kind="tool_call", tool="ghost",
                                  operation="x", args={}), CorrelationId("c"))
    assert not obs.ok and "unknown tool" in (obs.error or "")
