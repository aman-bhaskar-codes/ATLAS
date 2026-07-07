"""Inference runtime — one attempt against one model, fully governed.

ORDER (per attempt): breaker check -> rate limit -> budget check -> provider call
(timed) -> record health + telemetry -> reconcile spend. This is the only place
an attempt is executed; the fallback engine calls it per candidate.
"""

from __future__ import annotations

import time

from atlas.infra.logging import get_logger
from atlas.intelligence.contracts import (
    InferenceRequest,
    InferenceResponse,
    ModelSpec,
)
from atlas.intelligence.errors import ProviderError, RateLimitError
from atlas.intelligence.governance.cost_governor import CostGovernor
from atlas.intelligence.health.health_monitor import HealthMonitor
from atlas.intelligence.observability.telemetry import Telemetry
from atlas.intelligence.registry.provider_registry import ProviderRegistry
from atlas.intelligence.runtime.retry import RetryEngine

_log = get_logger("atlas.intel.inference")


class InferenceRuntime:
    def __init__(
        self, *, providers: ProviderRegistry, health: HealthMonitor,
        governor: CostGovernor, telemetry: Telemetry, model_timeout_s: float = 120.0,
    ) -> None:
        self._providers = providers
        self._health = health
        self._governor = governor
        self._telemetry = telemetry
        self._timeout_s = model_timeout_s
        self._retry = RetryEngine()

    async def attempt(self, req: InferenceRequest, spec: ModelSpec) -> InferenceResponse:
        provider = self._providers.get(spec.provider)
        if provider is None:
            raise ProviderError(f"no adapter for provider {spec.provider!r}")
        if not provider.is_local:
            projected = self._estimate(req, spec)
            await self._governor.check(projected, task_id=req.task_id)

        start = time.perf_counter()
        
        async def _call() -> InferenceResponse:
            comp = await provider.complete(
                model=spec.provider_model, messages=req.messages,
                max_tokens=req.max_tokens, temperature=req.temperature,
                usd_in=spec.usd_per_1m_input, usd_out=spec.usd_per_1m_output,
            )
            latency = int((time.perf_counter() - start) * 1000)
            return InferenceResponse(
                text=comp.text, model_id=spec.id, provider=spec.provider,
                usage=comp.usage, latency_ms=latency,
            )
            
        try:
            resp = await self._retry.run(_call)
        except (ProviderError, RateLimitError) as exc:
            latency = int((time.perf_counter() - start) * 1000)
            self._health.record(spec.provider, ok=False, latency_ms=latency)
            await self._telemetry.record_failure(req, spec, exc, latency)
            raise

        self._health.record(spec.provider, ok=True, latency_ms=resp.latency_ms)
        if req.task_id and resp.usage.usd:
            self._governor.record_task_spend(req.task_id, resp.usage.usd)
        await self._telemetry.record_success(req, spec, resp.usage, resp.latency_ms)
        return resp

    @staticmethod
    def _estimate(req: InferenceRequest, spec: ModelSpec) -> float:
        approx_in = sum(len(m.content) for m in req.messages) // 4
        return approx_in / 1e6 * spec.usd_per_1m_input + req.max_tokens / 1e6 * spec.usd_per_1m_output
