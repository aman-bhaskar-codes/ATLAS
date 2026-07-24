"""Capability dispatcher — the single, safety-gated execution path.

INVARIANT: a capability executes ONLY through guard(). The dispatcher builds the
(tool, operation) ToolRequest from the CapabilitySpec so L1 classifies/tiers/audits
it exactly like a Phase-2 tool. On approval it walks the ranked provider list
(fallback across providers) with per-provider retry, normalizes the raw result to a
domain model, and records health + telemetry. A Safety denial becomes a typed
CapabilityDenied result (information for the orchestrator), never a crash.
"""

from __future__ import annotations

import asyncio
import random
import time

from atlas.capabilities.domain.common import CapabilityResult, Provenance, SourceKind
from atlas.capabilities.errors import (
    CapabilityDenied,
    CapabilityError,
    NoProviderAvailable,
    ProviderExecutionError,
)
from atlas.capabilities.observability.telemetry import CapabilityTelemetry
from atlas.capabilities.providers.base import CapabilityRequest, Provider
from atlas.capabilities.registry.capability import CapabilityRegistry
from atlas.capabilities.registry.health import CapabilityHealth
from atlas.capabilities.registry.provider_registry import ProviderRegistry
from atlas.infra.ids import CorrelationId
from atlas.infra.logging import get_logger
from atlas.infra.types import ToolRequest, ToolResult
from atlas.safety.engine import DeniedError, HaltedError, SafetyEngine
from atlas.tools.base import Tool

_log = get_logger("atlas.cap.dispatch")


class _CapabilityTool:
    """Adapts a capability execution into the Tool protocol so the Safety Engine
    can guard it. dry_run() describes the intended side effect for Tier-2 preview;
    execute() runs the provider chain. WHY: L1 only knows Tools — so we present the
    capability AS a tool, inheriting the entire safety pipeline unchanged."""

    def __init__(self, name: str, run, preview: str) -> None:  # type: ignore
        self.name = name
        self._run = run
        self._preview = preview

    def dry_run(self, args: dict[str, object]) -> str:
        return self._preview

    async def execute(self, args: dict[str, object]) -> ToolResult:
        return await self._run()  # type: ignore


class CapabilityDispatcher:
    def __init__(
        self, *, registry: CapabilityRegistry, providers: ProviderRegistry,
        health: CapabilityHealth, safety: SafetyEngine, telemetry: CapabilityTelemetry,
    ) -> None:
        self._registry = registry
        self._providers = providers
        self._health = health
        self._safety = safety
        self._telemetry = telemetry

    async def execute(
        self, request: CapabilityRequest, correlation_id: CorrelationId,
        *, task_id: str | None = None,
    ) -> CapabilityResult[Any]:  # type: ignore
        spec = self._registry.get(request.capability)

        # provider chain resolved BEFORE the safety gate so dry_run preview is real
        try:
            chain = self._providers.candidates(request.capability)
        except NoProviderAvailable as exc:
            return CapabilityResult(ok=False, error=str(exc))

        preview = f"{spec.safety_tool}.{request.operation} via {chain[0].name} args={request.args}"

        async def run_chain() -> ToolResult:
            result = await self._walk_providers(chain, request, correlation_id, task_id)
            # smuggle the CapabilityResult out through ToolResult.output
            return ToolResult(ok=result.ok, output=result,
                              error=result.error)

        tool: Tool = _CapabilityTool(spec.safety_tool, run_chain, preview)
        safety_req = ToolRequest(
            correlation_id=correlation_id, tool=spec.safety_tool,
            operation=request.operation, args=dict(request.args),
        )
        try:
            tool_result = await self._safety.guard(safety_req, tool)
        except HaltedError as exc:
            return CapabilityResult(ok=False, error=f"halted: {exc}")
        except DeniedError as exc:
            # denial is information, not a crash
            raise CapabilityDenied(exc.decision.reason) from exc
        payload = tool_result.output
        if isinstance(payload, CapabilityResult):
            return payload
        return CapabilityResult(ok=tool_result.ok, error=tool_result.error)

    async def _walk_providers(
        self, chain: list[Provider], request: CapabilityRequest,
        correlation_id: CorrelationId, task_id: str | None,
    ) -> CapabilityResult[Any]:  # type: ignore
        last: Exception | None = None
        for i, provider in enumerate(chain):
            try:
                return await self._attempt(provider, request, correlation_id, task_id,
                                           fell_back=i > 0)
            except CapabilityError as exc:
                last = exc
                _log.warning("cap.fallback", event_type="cap", provider=provider.name,
                             error=repr(exc), remaining=len(chain) - i - 1)
                if not exc.provider_switch_helps and not exc.retryable:
                    break
        return CapabilityResult(ok=False, error=f"all providers failed; last={last!r}")

    async def _attempt(
        self, provider: Provider, request: CapabilityRequest,
        correlation_id: CorrelationId, task_id: str | None, *, fell_back: bool,
    ) -> CapabilityResult[Any]:  # type: ignore
        policy = provider.retry_policy()
        attempt = 0
        start = time.perf_counter()
        while True:
            attempt += 1
            try:
                raw = await provider.execute(request)
                payload = provider.normalize(raw)
                latency = int((time.perf_counter() - start) * 1000)
                self._health.record(provider.name, ok=True, latency_ms=latency)
                await self._telemetry.record(
                    correlation_id=str(correlation_id), capability=request.capability,
                    provider=provider.name, ok=True, latency_ms=latency, task_id=task_id)
                return CapabilityResult(
                    ok=True, payload=payload, provider=provider.name, latency_ms=latency,
                    provenance=(Provenance(
                        provider=provider.name,
                        source_kind=SourceKind.MCP if provider.name.startswith("mcp:")
                        else (SourceKind.LOCAL if provider.is_local else SourceKind.WEB)),),
                )
            except Exception as exc:
                latency = int((time.perf_counter() - start) * 1000)
                self._health.record(provider.name, ok=False, latency_ms=latency)
                await self._telemetry.record(
                    correlation_id=str(correlation_id), capability=request.capability,
                    provider=provider.name, ok=False, latency_ms=latency, task_id=task_id)
                if attempt >= policy.max_attempts:
                    if isinstance(exc, CapabilityError):
                        raise
                    raise ProviderExecutionError(f"{provider.name}: {exc}") from exc
                backoff = min(policy.base_backoff_s * (2 ** (attempt - 1)), policy.max_backoff_s)
                await asyncio.sleep(backoff + random.uniform(0, backoff * 0.25))
