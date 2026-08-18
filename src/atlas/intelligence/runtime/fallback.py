"""Fallback engine — walk the ranked model list on failure.

WHY the selector already returns a RANKED list: that ordered list IS the fallback
chain (Ollama -> Groq Free -> Gemini Free -> GLM -> graceful failure). The engine
tries each in order, respecting the breaker/rate-limiter, until one succeeds or
all fail (FallbackError). retry (within a provider) and fallback (across models)
are distinct: retry for transient same-provider blips, fallback for
provider-switch-helps failures.

ZERO-COST-FIRST: QuotaExhaustedError is now caught alongside IntelligenceError.
provider.fallback events are emitted to the MessageBus so the frontend/trajectory
can show the user exactly what happened.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

from atlas.infra.logging import get_logger
from atlas.intelligence.contracts import InferenceResponse, ModelSpec
from atlas.intelligence.errors import FallbackError, IntelligenceError

if TYPE_CHECKING:
    from atlas.infra.bus import MessageBus
    
from atlas.infra.bus import Event


class FallbackEvent(Event):
    provider: str
    model: str
    cost_class: str
    error: str
    remaining_candidates: int


_log = get_logger("atlas.intel.fallback")

Attempt = Callable[[ModelSpec], Awaitable[InferenceResponse]]


class FallbackEngine:
    def __init__(self, *, bus: MessageBus | None = None) -> None:
        self._bus = bus

    async def run(self, ranked: list[ModelSpec], attempt: Attempt) -> InferenceResponse:
        last: Exception | None = None
        for i, spec in enumerate(ranked):
            try:
                resp = await attempt(spec)
                return resp.model_copy(update={"fell_back": i > 0, "attempts": i + 1})
            except IntelligenceError as exc:
                last = exc
                _log.warning(
                    "fallback.next", event_type="intel", model=spec.id, error=repr(exc), remaining=len(ranked) - i - 1
                )
                if not exc.provider_switch_helps and not exc.retryable:
                    break  # e.g. budget exceeded — switching won't help
                # Emit fallback event for dashboard/trajectory visibility
                await self._emit_fallback(spec, exc, remaining=len(ranked) - i - 1)
        raise FallbackError(f"all candidates failed; last={last!r}")

    async def _emit_fallback(self, spec: ModelSpec, exc: Exception, *, remaining: int) -> None:
        if self._bus is None:
            return
        try:
            event = FallbackEvent(
                correlation_id="fallback",
                provider=spec.provider,
                model=spec.id,
                cost_class=spec.cost_class.value,
                error=repr(exc),
                remaining_candidates=remaining,
            )
            await self._bus.publish("provider.fallback", event)
        except Exception:
            pass  # best-effort
