"""ToolingFabric lifecycle tests — initialize/shutdown with isolation (§37/§56)."""

from __future__ import annotations

from atlas.capabilities.domain.common import SourceKind
from atlas.tooling.errors import ToolAdapterError, ToolInitializationError
from atlas.tooling.execution.executor import ToolingExecutor
from atlas.tooling.fabric import ToolingFabric
from atlas.tooling.models.identity import ToolNamespace, build_tool_id
from atlas.tooling.models.tool_definition import ExecutionType, Locality, UniversalToolDefinition
from atlas.tooling.models.tool_health import ToolRuntimeState
from atlas.tooling.models.tool_invocation import InvocationSource, UniversalToolInvocation
from atlas.tooling.models.tool_provenance import ToolProvenance
from atlas.tooling.models.tool_result import UniversalToolResult
from atlas.tooling.models.tool_schema import ToolInputSchema
from atlas.tooling.registry.registry import ToolingRegistry


class RecordingAdapter:
    """Real double: no-op initialize by default, records lifecycle calls."""

    kind = "recording"

    def __init__(self, *, fail_validation: bool = False, fail_initialize: bool = False) -> None:
        self._fail_validation = fail_validation
        self._fail_initialize = fail_initialize
        self.initialized = 0
        self.shutdowns = 0
        self.executions = 0

    async def initialize(self) -> None:
        self.initialized += 1
        if self._fail_initialize:
            raise ToolInitializationError("backend connection refused")

    async def validate(self, definition: UniversalToolDefinition) -> None:
        if self._fail_validation:
            raise ToolAdapterError("wiring missing")

    async def execute(
        self, invocation: UniversalToolInvocation, definition: UniversalToolDefinition
    ) -> UniversalToolResult:
        self.executions += 1
        return UniversalToolResult(ok=True, tool_id=invocation.tool_id, data="ok")

    async def health(self) -> bool:
        return True

    async def shutdown(self) -> None:
        self.shutdowns += 1


def _definition(name: str) -> UniversalToolDefinition:
    return UniversalToolDefinition(
        id=build_tool_id(ToolNamespace.NATIVE, "atlas", name),
        name=name,
        namespace=ToolNamespace.NATIVE,
        provider="atlas",
        operations=("op",),
        input_schema=ToolInputSchema.from_operations(("op",)),
        execution_type=ExecutionType.NATIVE,
        adapter="recording",
        provenance=ToolProvenance(source_kind=SourceKind.LOCAL, provider="atlas"),
        locality=Locality.LOCAL,
        safety_tool=name,
    )


def _fabric(*, fail_first: bool = False) -> tuple[ToolingFabric, ToolingRegistry, RecordingAdapter, RecordingAdapter]:
    registry = ToolingRegistry()
    first = RecordingAdapter(fail_initialize=fail_first)
    second = RecordingAdapter()
    registry.register(_definition("a"), first)  # type: ignore[arg-type]
    registry.register(_definition("b"), second)  # type: ignore[arg-type]
    fabric = ToolingFabric(registry=registry, executor=ToolingExecutor(registry=registry))
    return fabric, registry, first, second


async def test_initialize_marks_ready_and_reports() -> None:
    fabric, registry, first, second = _fabric()
    report = await fabric.initialize()
    assert report.ok
    assert set(report.ready) == {"native:atlas:a", "native:atlas:b"}
    assert first.initialized == 1 and second.initialized == 1
    for tool_id in ("native:atlas:a", "native:atlas:b"):
        assert registry.require(tool_id).status.state == ToolRuntimeState.READY


async def test_one_failed_adapter_does_not_block_others() -> None:
    """§56: broken adapter isolation with explicit partial readiness."""
    fabric, registry, _first, second = _fabric(fail_first=True)
    report = await fabric.initialize()

    assert not report.ok
    assert report.failed == ("native:atlas:a",)
    assert "connection refused" in report.failure_details["native:atlas:a"]
    assert report.ready == ("native:atlas:b",)  # unrelated tooling still started
    assert registry.require("native:atlas:a").status.state == ToolRuntimeState.FAILED
    assert registry.require("native:atlas:b").status.state == ToolRuntimeState.READY

    # The failed tool cannot execute; the healthy one can.
    invocation = UniversalToolInvocation(tool_id="native:atlas:a", operation="op", correlation_id="c")
    try:
        await fabric.executor.execute(invocation)
        raise AssertionError("expected ToolAdapterError")
    except ToolAdapterError:
        pass
    ok = await fabric.executor.execute(
        UniversalToolInvocation(
            tool_id="native:atlas:b",
            operation="op",
            correlation_id="c",
            source=InvocationSource.TEST,
        )
    )
    assert ok.ok is True
    assert second.executions == 1


async def test_disabled_tools_are_skipped_not_failed() -> None:
    fabric, registry, first, _second = _fabric()
    registry.disable("native:atlas:a")
    report = await fabric.initialize()

    assert report.skipped == ("native:atlas:a",)
    assert registry.require("native:atlas:a").status.state == ToolRuntimeState.DISABLED
    assert first.initialized == 0  # never touched
    assert registry.require("native:atlas:b").status.state == ToolRuntimeState.READY


async def test_shutdown_shares_adapters_and_isolates_errors() -> None:
    fabric, _registry, first, second = _fabric()
    await fabric.initialize()
    await fabric.shutdown()

    # One adapter instance serves both tools but shuts down exactly once.
    assert first.shutdowns == 1
    assert second.shutdowns == 1

    class ExplodingAdapter(RecordingAdapter):
        kind = "exploding"

        async def shutdown(self) -> None:
            self.shutdowns += 1
            raise RuntimeError("boom")

    registry2 = ToolingRegistry()
    good = RecordingAdapter()
    bad = ExplodingAdapter()
    registry2.register(_definition("c"), good)  # type: ignore[arg-type]
    registry2.register(_definition("d"), bad)  # type: ignore[arg-type]
    fabric2 = ToolingFabric(registry=registry2, executor=ToolingExecutor(registry=registry2))
    await fabric2.initialize()
    await fabric2.shutdown()  # must not raise despite the exploding adapter
    assert good.shutdowns == 1
    assert bad.shutdowns == 1


async def test_reinitialize_recovers_failed_tool() -> None:
    """Enabling a FAILED tool re-arms it for the next initialize pass."""
    fabric, registry, first, _second = _fabric(fail_first=True)
    await fabric.initialize()
    assert registry.require("native:atlas:a").status.state == ToolRuntimeState.FAILED

    first._fail_initialize = False  # the underlying problem is "fixed"
    registry.enable("native:atlas:a")
    report = await fabric.initialize()
    assert report.ok
    assert registry.require("native:atlas:a").status.state == ToolRuntimeState.READY
