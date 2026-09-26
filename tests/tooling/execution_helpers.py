"""Shared harness for execution-fabric tests: a REAL governed graph.

Builds the Part-1 funnel (ToolRegistry → ToolDispatcher → real SafetyEngine →
fake tools), the Part-1 ToolingExecutor over it, and the Part-2 catalog — the
exact stack the ExecutionEngine must drive. No bypasses: every step executes
through SafetyEngine.guard.
"""

from __future__ import annotations

import asyncio
import datetime
from types import SimpleNamespace
from typing import Any

from atlas.infra.config import ExecutionCfg, SafetyCfg
from atlas.infra.db import Database
from atlas.infra.types import ToolResult
from atlas.orchestration.dispatcher import ToolDispatcher
from atlas.orchestration.registry import ToolMetadata, ToolRegistry
from atlas.safety.audit import AuditLog
from atlas.safety.classifier import TierClassifier
from atlas.safety.engine import SafetyEngine
from atlas.safety.manifest import Manifest
from atlas.safety.policy import KillSwitchPolicy, PolicyEngine
from atlas.tooling.adapters.native import NativeToolAdapter, native_definition
from atlas.tooling.catalog.catalog import ToolCatalog
from atlas.tooling.catalog.store import ToolCatalogStore
from atlas.tooling.execution import ExecutionRunStore, TerminalOutcome
from atlas.tooling.execution.engine import ExecutionEngine
from atlas.tooling.execution.executor import ToolingExecutor
from atlas.tooling.fabric import ToolingFabric
from atlas.tooling.models.tool_health import ToolRuntimeState
from atlas.tooling.registry.registry import ToolingRegistry
from atlas.tooling.routing.models import RoutePlan, RouteStep
from tests.fakes import FakeClock, FakeKillSwitch


class ProbeTool:
    """Configurable Tool-protocol double: fails the first N calls."""

    name = "audit_probe"

    def __init__(self, fail_first: int = 0) -> None:
        self.calls = 0
        self.fail_first = fail_first

    def dry_run(self, args: dict[str, Any]) -> str:
        return "audit probe"

    async def execute(self, args: dict[str, Any]) -> ToolResult:
        self.calls += 1
        if self.calls <= self.fail_first:
            return ToolResult(ok=False, error="connection glitch (transient upstream error)", duration_ms=5)
        return ToolResult(ok=True, output=f"probe-{self.calls}")


class SlowTool:
    """Stays running until released — for cancellation/timeout tests."""

    name = "audit_slow"

    def __init__(self) -> None:
        self.calls = 0
        self.release = asyncio.Event()

    def dry_run(self, args: dict[str, Any]) -> str:
        return "slow probe"

    async def execute(self, args: dict[str, Any]) -> ToolResult:
        self.calls += 1
        try:
            await asyncio.wait_for(self.release.wait(), timeout=0.3)
        except TimeoutError:
            pass
        return ToolResult(ok=True, output="slow-done")


class SendTool:
    """A side-effecting, non-idempotent tool (§24): the catalog marks it
    side_effects=True via metadata."""

    name = "audit_send"

    def __init__(self) -> None:
        self.calls = 0

    def dry_run(self, args: dict[str, Any]) -> str:
        return "send"

    async def execute(self, args: dict[str, Any]) -> ToolResult:
        self.calls += 1
        return ToolResult(ok=False, error="connection lost after backend send", duration_ms=5)


class AuthTool:
    """Fails with an auth error — routes to WAITING_HUMAN via recovery (§52)."""

    name = "audit_auth"

    def __init__(self) -> None:
        self.calls = 0

    def dry_run(self, args: dict[str, Any]) -> str:
        return "auth probe"

    async def execute(self, args: dict[str, Any]) -> ToolResult:
        self.calls += 1
        return ToolResult(ok=False, error="ProviderAuthError: credential missing", duration_ms=5)


class Harness:
    def __init__(self, db: Database) -> None:
        self.db = db
        manifest = Manifest(
            version=1,
            allowed_paths={},
            allowed_commands={},
            whatsapp={},
            safety={},
            rules=[{"tool": "audit_*", "operation": "*", "tier": 0}],  # type: ignore[list-item]
            hard_block=[],  # type: ignore[arg-type]
        )
        audit = AuditLog(db)
        killswitch = FakeKillSwitch(False)
        self.safety = SafetyEngine(
            classifier=TierClassifier(manifest, 2),
            policy=PolicyEngine((KillSwitchPolicy(killswitch),)),  # type: ignore[arg-type]
            audit=audit,
            killswitch=killswitch,
            clock=FakeClock(datetime.datetime.now()),  # type: ignore[arg-type]
            cfg=SafetyCfg(),
        )
        self.orch_registry = ToolRegistry()
        self.dispatcher = ToolDispatcher(self.orch_registry, self.safety)
        self.registry = ToolingRegistry()
        self.tools: dict[str, Any] = {}

    def register_tool(self, tool: Any, *, operations: tuple[str, ...], side_effects: bool = False) -> str:
        self.tools[tool.name] = tool
        self.orch_registry.register(
            tool,
            operations,
            ToolMetadata(
                name=tool.name,
                operations=operations,
                side_effects=side_effects,
                idempotent=not side_effects,
            ),
        )
        tool_id = f"native:atlas:{tool.name}"
        definition = native_definition(tool.name, operations, self.orch_registry.metadata(tool.name))
        self.registry.register(
            definition,
            NativeToolAdapter(dispatcher=self.dispatcher, tool_registry=self.orch_registry),
        )
        self.registry.set_status(tool_id, ToolRuntimeState.READY)
        return tool_id

    async def build_engine(self, *, config: ExecutionCfg | None = None, bus: Any = None) -> ExecutionEngine:
        fabric = ToolingFabric(registry=self.registry, executor=ToolingExecutor(registry=self.registry))
        await fabric.initialize()
        catalog = ToolCatalog(store=ToolCatalogStore(self.db), registry=self.registry)
        await catalog.initialize()
        return ExecutionEngine(
            config=config or ExecutionCfg(),
            store=ExecutionRunStore(self.db),
            tooling_executor=fabric.executor,
            catalog=catalog,
            bus=bus,
        )


def plan_of(*steps: RouteStep, plan_id: str = "plan", strategy: str = "SEQUENTIAL") -> RoutePlan:
    return RoutePlan(
        plan_id=plan_id,
        task_id="t",
        route_id="r",
        strategy=strategy,
        domain="general",
        steps=steps,
    )


def step(step_id: str, candidate_id: str, operation: str = "run", **kwargs: Any) -> RouteStep:
    return RouteStep(step_id=step_id, candidate_id=candidate_id, operation=operation, **kwargs)


def fake_decision(fallbacks: tuple[str, ...] = ()) -> SimpleNamespace:
    return SimpleNamespace(fallback_candidates=fallbacks)


__all__ = [
    "AuthTool",
    "Harness",
    "ProbeTool",
    "SendTool",
    "SlowTool",
    "TerminalOutcome",
    "fake_decision",
    "plan_of",
    "step",
]
