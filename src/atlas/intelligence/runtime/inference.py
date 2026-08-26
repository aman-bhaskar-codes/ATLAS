"""Inference runtime — one attempt against one model, fully governed.

ORDER (per attempt): breaker check -> rate limit -> QUOTA CHECK -> budget check
-> provider call (timed) -> record health + telemetry + QUOTA USAGE -> reconcile
spend. This is the only place an attempt is executed; the fallback engine calls
it per candidate.

ZERO-COST-FIRST ADDITIONS:
 - FreeQuotaGovernor.check() before each attempt for FREE_QUOTA providers
 - FreeQuotaGovernor.record() after success
 - QuotaExhaustedError is caught alongside ProviderError/RateLimitError
 - Provider lifecycle events emitted to MessageBus
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from atlas.infra.logging import get_logger
from atlas.infra.types import CostClass
from atlas.intelligence.contracts import (
    InferenceRequest,
    InferenceResponse,
    ModelSpec,
)
from atlas.intelligence.errors import ProviderError, QuotaExhaustedError, RateLimitError
from atlas.intelligence.governance.cost_governor import CostGovernor
from atlas.intelligence.governance.quota_governor import FreeQuotaGovernor
from atlas.intelligence.health.health_monitor import HealthMonitor
from atlas.intelligence.observability.telemetry import Telemetry
from atlas.intelligence.registry.provider_registry import ProviderRegistry
from atlas.intelligence.runtime.events import ProviderLifecycleEvent
from atlas.intelligence.runtime.retry import RetryEngine

if TYPE_CHECKING:
    from atlas.infra.bus import MessageBus
    from atlas.infra.llm_tracker import LLMCallTracker

_log = get_logger("atlas.intel.inference")


class InferenceRuntime:
    def __init__(
        self,
        *,
        providers: ProviderRegistry,
        health: HealthMonitor,
        governor: CostGovernor,
        quota_governor: FreeQuotaGovernor | None = None,
        telemetry: Telemetry,
        model_timeout_s: float = 120.0,
        tracker: LLMCallTracker | None = None,
        bus: MessageBus | None = None,
    ) -> None:
        self._providers = providers
        self._health = health
        self._governor = governor
        self._quota = quota_governor
        self._telemetry = telemetry
        self._timeout_s = model_timeout_s
        self._retry = RetryEngine()
        self._tracker = tracker
        self._bus = bus

    async def attempt(self, req: InferenceRequest, spec: ModelSpec) -> InferenceResponse:
        provider = self._providers.get(spec.provider)
        if provider is None:
            raise ProviderError(f"no adapter for provider {spec.provider!r}")

        # --- Pre-flight checks ---

        # Quota check for free-tier providers
        if self._quota and spec.cost_class == CostClass.FREE_QUOTA:
            estimated_tokens = self._estimate_tokens(req, spec)
            try:
                self._quota.check(spec.provider, estimated_tokens)
            except QuotaExhaustedError:
                await self._emit_event("provider.quota_exhausted", spec, req)
                raise

        # Budget check for paid providers
        if not provider.is_local:
            projected = self._estimate_cost(req, spec)
            await self._governor.check(projected, task_id=req.task_id)

        # --- Provider call ---
        await self._emit_event("provider.selected", spec, req)
        start = time.perf_counter()

        async def _call() -> InferenceResponse:
            comp = await provider.complete(
                model=spec.provider_model,
                messages=req.messages,
                max_tokens=req.max_tokens,
                temperature=req.temperature,
                usd_in=spec.usd_per_1m_input,
                usd_out=spec.usd_per_1m_output,
                tools=req.tools,
            )
            latency = int((time.perf_counter() - start) * 1000)
            return InferenceResponse(
                text=comp.text,
                model_id=spec.id,
                provider=spec.provider,
                usage=comp.usage,
                latency_ms=latency,
                tool_calls=comp.tool_calls,
                reasoning_details=comp.reasoning_details,
            )

        try:
            resp = await self._retry.run(_call)
        except RateLimitError as exc:
            latency = int((time.perf_counter() - start) * 1000)
            self._health.record(spec.provider, ok=False, latency_ms=latency)
            await self._telemetry.record_failure(req, spec, exc, latency)
            await self._emit_event("provider.rate_limited", spec, req, error=str(exc))
            raise
        except (ProviderError, QuotaExhaustedError) as exc:
            latency = int((time.perf_counter() - start) * 1000)
            self._health.record(spec.provider, ok=False, latency_ms=latency)
            await self._telemetry.record_failure(req, spec, exc, latency)
            await self._emit_event("provider.failed", spec, req, error=str(exc))
            raise

        # --- Post-call bookkeeping ---
        self._health.record(spec.provider, ok=True, latency_ms=resp.latency_ms)

        # Record quota usage for free-tier providers
        if self._quota and spec.cost_class == CostClass.FREE_QUOTA:
            total_tokens = resp.usage.input_tokens + resp.usage.output_tokens
            self._quota.record(spec.provider, total_tokens)

        if req.task_id and resp.usage.usd:
            self._governor.record_task_spend(req.task_id, resp.usage.usd)
        await self._telemetry.record_success(req, spec, resp.usage, resp.latency_ms)

        # Phase 0: Record every successful inference call for cost analysis
        if self._tracker is not None:
            try:
                await self._tracker.record(
                    task_id=req.task_id or req.correlation_id,
                    provider=spec.provider,
                    model=spec.id,
                    tokens_in=resp.usage.input_tokens,
                    tokens_out=resp.usage.output_tokens,
                    cost_usd=resp.usage.usd,
                    latency_ms=resp.latency_ms,
                )
            except Exception:
                pass  # tracker is best-effort; never block inference

        return resp

    async def close(self) -> None:
        await self._providers.close()

    def provider_names(self) -> list[str]:
        """Return list of all registered provider names.

        Public accessor for diagnostics and health checks.
        """
        return self._providers.names()

    def is_provider_available(self, name: str) -> bool:
        """Check if a provider is currently available (not circuit-broken).

        Public accessor for diagnostics and health checks.
        """
        return self._health.is_available(name)

    @staticmethod
    def _estimate_cost(req: InferenceRequest, spec: ModelSpec) -> float:
        approx_in = sum(len(m.content) for m in req.messages) // 4
        return approx_in / 1e6 * spec.usd_per_1m_input + req.max_tokens / 1e6 * spec.usd_per_1m_output

    @staticmethod
    def _estimate_tokens(req: InferenceRequest, spec: ModelSpec) -> int:
        """Rough token estimate for quota pre-check."""
        approx_in = sum(len(m.content) for m in req.messages) // 4
        return approx_in + spec.context_length // 4  # conservative

    async def _emit_event(
        self,
        event_type: str,
        spec: ModelSpec,
        req: InferenceRequest,
        *,
        error: str | None = None,
    ) -> None:
        """Emit provider lifecycle events to the MessageBus for
        dashboard visibility and trajectory telemetry."""
        if self._bus is None:
            return
        try:
            event = ProviderLifecycleEvent(
                correlation_id=req.correlation_id,
                kind=event_type,
                provider=spec.provider,
                model=spec.id,
                cost_class=spec.cost_class.value,
                task_id=req.task_id,
                error=error,
            )
            await self._bus.publish(event_type, event)
        except Exception:
            pass  # event emission is best-effort
