"""Fallback engine — walk the ranked model list on failure.

WHY the selector already returns a RANKED list: that ordered list IS the fallback
chain (DeepSeek -> GLM -> Gemini -> local Qwen -> graceful failure). The engine
tries each in order, respecting the breaker/rate-limiter, until one succeeds or
all fail (FallbackError). retry (within a provider) and fallback (across models)
are distinct: retry for transient same-provider blips, fallback for
provider-switch-helps failures.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from atlas.infra.logging import get_logger
from atlas.intelligence.contracts import InferenceResponse, ModelSpec
from atlas.intelligence.errors import FallbackError, IntelligenceError

_log = get_logger("atlas.intel.fallback")

Attempt = Callable[[ModelSpec], Awaitable[InferenceResponse]]


class FallbackEngine:
    async def run(self, ranked: list[ModelSpec], attempt: Attempt) -> InferenceResponse:
        last: Exception | None = None
        for i, spec in enumerate(ranked):
            try:
                resp = await attempt(spec)
                return resp.model_copy(update={"fell_back": i > 0, "attempts": i + 1})
            except IntelligenceError as exc:
                last = exc
                _log.warning("fallback.next", event_type="intel", model=spec.id,
                             error=repr(exc), remaining=len(ranked) - i - 1)
                if not exc.provider_switch_helps and not exc.retryable:
                    break  # e.g. budget exceeded — switching won't help
        raise FallbackError(f"all candidates failed; last={last!r}")
