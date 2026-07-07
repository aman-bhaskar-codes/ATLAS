"""ModelGateway — the ONE egress. Supersedes the Phase-1 gateway internals while
keeping a compatible complete() for existing callers.

FLOW: router (required capabilities) -> selector (ranked models under
constraints/health) -> fallback engine (walk the chain) -> inference runtime
(governed attempt). A compatibility method complete_legacy() accepts the old
ModelRequest so Phases 3/4/4.5 keep working unchanged.
"""

from __future__ import annotations

from typing import Any

from atlas.intelligence.contracts import InferenceRequest, InferenceResponse
from atlas.intelligence.runtime.fallback import FallbackEngine
from atlas.intelligence.runtime.inference import InferenceRuntime
from atlas.intelligence.selection.router import CapabilityRouter
from atlas.intelligence.selection.selector import ModelSelector


class ModelGateway:
    def __init__(
        self, *, router: CapabilityRouter, selector: ModelSelector,
        fallback: FallbackEngine, runtime: InferenceRuntime,
    ) -> None:
        self._router = router
        self._selector = selector
        self._fallback = fallback
        self._runtime = runtime

    async def close(self) -> None:
        await self._runtime._providers.close()

    async def health(self) -> dict[str, bool]:
        """Backward compatibility for diagnostics/doctor.py"""
        status: dict[str, bool] = {}
        for p in self._runtime._providers.names():
            status[p] = self._runtime._health.is_available(p)
        return status

    async def infer(self, req: InferenceRequest) -> InferenceResponse:
        required = self._router.required(req)
        ranked = self._selector.select(required, req.constraints)
        return await self._fallback.run(ranked, lambda spec: self._runtime.attempt(req, spec))

    # --- Phase 1-4 compatibility: accept the old ModelRequest shape ---
    async def complete(self, model_request: Any) -> Any:
        """Adapter for existing callers (orchestrator/planner/critique).
        Maps ModelRequest -> InferenceRequest, infers, maps back to ModelResponse.
        WHY: zero churn upstream while the platform underneath is replaced."""
        from atlas.infra.types import ModelResponse, ModelTarget, TokenCost
        from atlas.intelligence.contracts import Constraints, Message, Role
        
        mr = model_request  # ModelRequest
        caps: frozenset[Any] = frozenset()
        constraints = Constraints(prefer_local=not getattr(mr, "needs_deep_reasoning", False))
        messages = []
        if getattr(mr, "system", None):
            messages.append(Message(role=Role.SYSTEM, content=mr.system))
        messages.append(Message(role=Role.USER, content=mr.prompt))
        req = InferenceRequest(
            correlation_id=mr.correlation_id,
            messages=messages, required_capabilities=caps, constraints=constraints,
            max_tokens=mr.max_tokens, temperature=mr.temperature,
        )
        resp = await self.infer(req)
        target = ModelTarget.CLOUD if resp.usage.usd > 0 else ModelTarget.LOCAL_FAST
        return ModelResponse(
            text=resp.text, target=target, model=resp.model_id,
            cost=TokenCost(input_tokens=resp.usage.input_tokens,
                           output_tokens=resp.usage.output_tokens, usd=resp.usage.usd),
            latency_ms=resp.latency_ms,
        )
