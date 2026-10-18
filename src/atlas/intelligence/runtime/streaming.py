"""Streaming runtime — governed asynchronous streaming chunks.

Like inference.py, but yields StreamChunks instead of awaiting the full text.
Accumulates usage and updates telemetry upon stream completion.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator

from atlas.infra.logging import get_logger
from atlas.intelligence.contracts import InferenceRequest, ModelSpec, StreamChunk, Usage
from atlas.intelligence.errors import ProviderError, RateLimitError
from atlas.intelligence.governance.cost_governor import CostGovernor
from atlas.intelligence.health.health_monitor import HealthMonitor
from atlas.intelligence.observability.telemetry import Telemetry
from atlas.intelligence.registry.provider_registry import ProviderRegistry

_log = get_logger("atlas.intel.streaming")


class StreamingRuntime:
    def __init__(
        self,
        *,
        providers: ProviderRegistry,
        health: HealthMonitor,
        governor: CostGovernor,
        telemetry: Telemetry,
        model_timeout_s: float = 120.0,
    ) -> None:
        self._providers = providers
        self._health = health
        self._governor = governor
        self._telemetry = telemetry
        self._timeout_s = model_timeout_s

    async def attempt(self, req: InferenceRequest, spec: ModelSpec) -> AsyncIterator[StreamChunk]:
        provider = self._providers.get(spec.provider)
        if provider is None:
            raise ProviderError(f"no adapter for provider {spec.provider!r}")
        if not provider.is_local:
            projected = self._estimate(req, spec)
            await self._governor.check(projected, task_id=req.task_id)

        start = time.perf_counter()
        deadline = start + self._timeout_s
        # we don't currently retry streaming requests in-band (more complex with yields).
        # fallback handles it if it fails early.
        stream: AsyncIterator[StreamChunk] | None = None
        try:
            stream = provider.stream(
                model=spec.provider_model,
                messages=req.messages,
                max_tokens=req.max_tokens,
                temperature=req.temperature,
            )
            while True:
                try:
                    # Apply the total deadline only while advancing the provider;
                    # a timeout context spanning yield would cancel the consumer.
                    remaining = deadline - time.perf_counter()
                    if remaining <= 0:
                        raise TimeoutError
                    async with asyncio.timeout(remaining):
                        chunk = await anext(stream)
                except StopAsyncIteration:
                    break
                except TimeoutError as exc:
                    raise ProviderError(f"model {spec.id!r} timed out after {self._timeout_s}s") from exc
                yield chunk
        except (ProviderError, RateLimitError) as exc:
            latency = int((time.perf_counter() - start) * 1000)
            self._health.record(spec.provider, ok=False, latency_ms=latency)
            await self._telemetry.record_failure(req, spec, exc, latency)
            raise
        finally:
            close = getattr(stream, "aclose", None) if stream is not None else None
            if close is not None:
                await close()

        latency = int((time.perf_counter() - start) * 1000)
        self._health.record(spec.provider, ok=True, latency_ms=latency)

        # calculate approximate usage
        approx_in = sum(len(m.content) for m in req.messages) // 4
        # Since we don't get exact output tokens from all providers in stream mode easily,
        # we estimate or require providers to send it as a final block. For now:
        approx_out = 0  # ideally track yielded text length
        usd = approx_in / 1e6 * spec.usd_per_1m_input + approx_out / 1e6 * spec.usd_per_1m_output
        usage = Usage(input_tokens=approx_in, output_tokens=approx_out, usd=usd)

        if req.task_id and usage.usd:
            self._governor.record_task_spend(req.task_id, usage.usd)
        await self._telemetry.record_success(req, spec, usage, latency)

    @staticmethod
    def _estimate(req: InferenceRequest, spec: ModelSpec) -> float:
        approx_in = sum(len(m.content) for m in req.messages) // 4
        return approx_in / 1e6 * spec.usd_per_1m_input + req.max_tokens / 1e6 * spec.usd_per_1m_output
