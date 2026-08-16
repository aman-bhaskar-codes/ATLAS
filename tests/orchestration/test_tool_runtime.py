"""Batch 3 tool-runtime tests — metadata, health, routing, native tool calling."""

from __future__ import annotations

from typing import Any

from atlas.infra.types import (
    ModelCapability,
    ModelRequest,
    ModelResponse,
    ProviderToolCall,
    ToolCallSpec,
)
from atlas.orchestration.registry import ToolMetadata, ToolRegistry
from atlas.orchestration.tool_routing import ToolHealthTracker, ToolRouter


class _FakeTool:
    def __init__(self, name: str) -> None:
        self.name = name

    def dry_run(self, args: dict[str, Any]) -> str:
        return f"{self.name} preview"

    async def execute(self, args: dict[str, Any]) -> Any:
        raise NotImplementedError


class TestToolRegistryMetadata:
    def test_register_with_metadata(self) -> None:
        reg = ToolRegistry()
        meta = ToolMetadata(
            name="filesystem",
            operations=("read", "write"),
            description="files",
            estimated_latency_ms=50,
            idempotent=False,
            side_effects=True,
        )
        reg.register(_FakeTool("filesystem"), ("read", "write"), meta)
        assert reg.metadata("filesystem") is meta
        assert reg.all_metadata()["filesystem"].side_effects is True
        assert reg.metadata("nope") is None

    def test_register_without_metadata(self) -> None:
        reg = ToolRegistry()
        reg.register(_FakeTool("shell"), ("read_only",))
        assert reg.metadata("shell") is None
        assert reg.get("shell") is not None

    def test_tool_call_specs_shape(self) -> None:
        reg = ToolRegistry()
        reg.register(
            _FakeTool("filesystem"),
            ("read", "write"),
            ToolMetadata(name="filesystem", description="files"),
        )
        specs = reg.tool_call_specs()
        assert len(specs) == 1
        spec = specs[0]
        assert isinstance(spec, ToolCallSpec)
        assert spec.name == "filesystem"
        params = spec.parameters
        assert params["required"] == ["operation"]
        assert set(params["properties"]) == {"operation", "args"}


class TestToolHealthTracker:
    def test_unknown_tool_neutral(self) -> None:
        t = ToolHealthTracker()
        assert t.health("mystery") == 0.5

    def test_success_ewma_rises_and_falls(self) -> None:
        t = ToolHealthTracker(alpha=0.5)
        for _ in range(6):
            t.record("t", ok=True, latency_ms=10)
        assert t.health("t") > 0.95
        for _ in range(8):
            t.record("t", ok=False, latency_ms=10)
        assert t.health("t") < 0.1

    def test_latency_ewma(self) -> None:
        t = ToolHealthTracker(alpha=0.5)
        t.record("t", ok=True, latency_ms=100)
        t.record("t", ok=True, latency_ms=200)
        assert 100 < t.latency_ms("t") <= 200

    def test_snapshot(self) -> None:
        t = ToolHealthTracker()
        t.record("a", ok=True, latency_ms=5)
        snap = t.snapshot()
        assert snap["a"]["calls"] == 1


class TestToolRouter:
    def _registry(self) -> ToolRegistry:
        reg = ToolRegistry()
        reg.register(
            _FakeTool("filesystem"),
            ("read", "write"),
            ToolMetadata(
                name="filesystem", description="files", estimated_latency_ms=50, idempotent=False, side_effects=True
            ),
        )
        reg.register(
            _FakeTool("knowledge"),
            ("search",),
            ToolMetadata(
                name="knowledge", description="search", estimated_latency_ms=200, idempotent=True, side_effects=False
            ),
        )
        return reg

    def test_healthy_cheap_tool_ranks_first(self) -> None:
        reg = self._registry()
        health = ToolHealthTracker()
        health.record("knowledge", ok=True, latency_ms=50)
        health.record("filesystem", ok=False, latency_ms=5000)
        router = ToolRouter(reg, health)
        assert router.rank()[0] == "knowledge"

    def test_side_effect_penalty(self) -> None:
        reg = self._registry()
        router = ToolRouter(reg, ToolHealthTracker())
        ranked = router.rank()
        assert ranked.index("knowledge") < ranked.index("filesystem")

    def test_catalog_includes_metadata_hints(self) -> None:
        router = ToolRouter(self._registry(), ToolHealthTracker())
        catalog = router.catalog()
        assert "idempotent" in catalog
        assert "side-effects" in catalog
        assert "knowledge" in catalog


class TestNativeToolCalling:
    async def test_end_to_end_tools_flow(self) -> None:
        """ModelRequest.tools → gateway → provider payload → tool_calls back."""
        from atlas.intelligence.contracts import Usage
        from atlas.intelligence.gateway import ModelGateway

        captured: dict[str, Any] = {}

        class _Provider:
            name = "fake"
            is_local = True

            async def complete(
                self,
                *,
                model: str,
                messages: Any,
                max_tokens: int,
                temperature: float,
                usd_in: float,
                usd_out: float,
                tools: Any = (),
            ) -> Any:
                captured["tools"] = list(tools)
                captured["model"] = model
                from atlas.intelligence.providers.base import ProviderCompletion

                return ProviderCompletion(
                    "thinking...",
                    Usage(input_tokens=1, output_tokens=1),
                    (
                        ProviderToolCall(
                            id="c1", name="filesystem", arguments={"operation": "read", "args": {"path": "/x"}}
                        ),
                    ),
                )

            async def health(self) -> bool:
                return True

            async def close(self) -> None:
                return None

        class _Registry:
            def get(self, name: str) -> _Provider:
                return _Provider()

            def names(self) -> list[str]:
                return ["fake"]

        from atlas.intelligence.governance.budget import Budgets
        from atlas.intelligence.governance.cost_governor import CostGovernor
        from atlas.intelligence.health.health_monitor import HealthMonitor
        from atlas.intelligence.observability.telemetry import Telemetry
        from atlas.intelligence.registry.capability_index import CapabilityIndex
        from atlas.intelligence.registry.model_registry import ModelRegistry
        from atlas.intelligence.registry.provider_registry import ProviderRegistry
        from atlas.intelligence.runtime.fallback import FallbackEngine
        from atlas.intelligence.runtime.inference import InferenceRuntime
        from atlas.intelligence.selection.router import CapabilityRouter
        from atlas.intelligence.selection.selector import ModelSelector

        # Build a minimal real gateway around the fake provider.
        provider_registry = ProviderRegistry()
        provider_registry.register(_Provider())
        from pathlib import Path

        models_yaml = Path(__file__).resolve().parents[2] / "config" / "models.yaml"
        # Pick/patch one local model to be served by the fake provider.
        model_registry = ModelRegistry.from_yaml(models_yaml)
        spec = next(iter(model_registry.all()))
        spec = spec.model_copy(
            update={
                "provider": "fake",
                "capabilities": frozenset(
                    {
                        ModelCapability.REASONING,
                        ModelCapability.TOOL_CALLING,
                    }
                ),
            }
        )
        index = CapabilityIndex(ModelRegistry({spec.id: spec}))

        health = HealthMonitor()

        async def _no_audit(*a: object, **k: object) -> None:
            return None

        telemetry = Telemetry(_no_audit)  # type: ignore[arg-type]
        runtime = InferenceRuntime(
            providers=provider_registry,
            health=health,
            governor=CostGovernor(spend=None, budgets=Budgets()),  # type: ignore[arg-type]
            telemetry=telemetry,
        )
        gateway = ModelGateway(
            router=CapabilityRouter(),
            selector=ModelSelector(index, health),
            fallback=FallbackEngine(),
            runtime=runtime,
        )

        tools = (ToolCallSpec(name="filesystem", description="files", parameters={"type": "object", "properties": {}}),)
        resp = await gateway.complete(
            ModelRequest(
                correlation_id="corr-1",  # type: ignore[arg-type]
                prompt="read /x",
                required_capabilities=frozenset({ModelCapability.TOOL_CALLING}),
                tools=tools,
            )
        )
        assert isinstance(resp, ModelResponse)
        assert len(captured["tools"]) == 1
        assert captured["tools"][0].name == "filesystem"
        assert resp.tool_calls and resp.tool_calls[0].name == "filesystem"
        assert resp.tool_calls[0].arguments["operation"] == "read"

    def test_openai_payload_includes_tools(self) -> None:
        from atlas.intelligence.contracts import Message, Role
        from atlas.intelligence.providers.openai_compatible import OpenAICompatibleProvider

        p = OpenAICompatibleProvider.__new__(OpenAICompatibleProvider)
        p._is_openrouter = False
        payload = p._payload(
            "m",
            [Message(role=Role.USER, content="hi")],
            100,
            0.2,
            False,
            (ToolCallSpec(name="t", description="d", parameters={"type": "object", "properties": {}}),),
        )
        assert payload["tools"][0]["function"]["name"] == "t"
        assert payload["tool_choice"] == "auto"

    def test_openai_parse_tool_calls(self) -> None:
        from atlas.intelligence.providers.openai_compatible import OpenAICompatibleProvider

        data = {
            "choices": [
                {
                    "message": {
                        "tool_calls": [
                            {"id": "1", "function": {"name": "fs", "arguments": '{"operation": "read"}'}},
                            {"id": "2", "function": {"name": "bad", "arguments": "not-json"}},
                        ]
                    }
                }
            ]
        }
        calls = OpenAICompatibleProvider._parse_tool_calls(data)
        assert len(calls) == 1
        assert calls[0].name == "fs" and calls[0].arguments == {"operation": "read"}

    def test_capability_router_requires_tool_calling(self) -> None:
        from atlas.intelligence.contracts import Constraints, InferenceRequest, Message, Role
        from atlas.intelligence.selection.router import CapabilityRouter

        req = InferenceRequest(
            correlation_id="c",  # type: ignore[arg-type]
            messages=[Message(role=Role.USER, content="x")],
            required_capabilities=frozenset({ModelCapability.REASONING}),
            constraints=Constraints(),
            tools=(ToolCallSpec(name="t"),),
        )
        caps = CapabilityRouter().required(req)
        assert ModelCapability.TOOL_CALLING in caps
