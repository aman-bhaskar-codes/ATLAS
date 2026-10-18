"""ModelGateway — the ONE egress. Supersedes the Phase-1 gateway internals while
keeping a compatible complete() for existing callers.

FLOW: router (required capabilities) -> selector (ranked models under
constraints/health) -> fallback engine (walk the chain) -> inference runtime
(governed attempt). A compatibility method complete_legacy() accepts the old
ModelRequest so Phases 3/4/4.5 keep working unchanged.
"""

from __future__ import annotations

from typing import Any

from atlas.infra.cognition import ModelTier
from atlas.infra.types import CostClass, CostPolicy, ModelCapability, NetworkPolicy, PrivacyClass
from atlas.intelligence.cache import SemanticCache
from atlas.intelligence.contracts import Constraints, InferenceRequest, InferenceResponse
from atlas.intelligence.runtime.fallback import FallbackEngine
from atlas.intelligence.runtime.inference import InferenceRuntime
from atlas.intelligence.selection.router import CapabilityRouter
from atlas.intelligence.selection.selector import ModelSelector

# Strictness order. WHY explicit rather than enum order: PrivacyClass is a
# StrEnum, so comparing members compares strings alphabetically — "public"
# would outrank "private". An accidental alphabetical max() here would widen
# egress for private data, which is the one direction that must never happen.
_PRIVACY_RANK: dict[PrivacyClass, int] = {
    PrivacyClass.PUBLIC: 0,
    PrivacyClass.INTERNAL: 1,
    PrivacyClass.PRIVATE: 2,
    PrivacyClass.SENSITIVE: 3,
    PrivacyClass.SECRET: 4,
}


_COST_RANK = {
    CostPolicy.UNRESTRICTED: 0,
    CostPolicy.BALANCED: 1,
    CostPolicy.FREE_PREFERRED: 2,
    CostPolicy.FREE_ONLY: 3,
    CostPolicy.ZERO_COST: 4,
}
_NETWORK_RANK = {
    NetworkPolicy.UNRESTRICTED: 0,
    NetworkPolicy.FREE_CLOUD: 1,
    NetworkPolicy.LOCAL_ONLY: 2,
    NetworkPolicy.OFFLINE: 3,
}


def _strictest_privacy(a: PrivacyClass, b: PrivacyClass) -> PrivacyClass:
    return a if _PRIVACY_RANK.get(a, 0) >= _PRIVACY_RANK.get(b, 0) else b


class ModelGateway:
    def __init__(
        self,
        *,
        router: CapabilityRouter,
        selector: ModelSelector,
        fallback: FallbackEngine,
        runtime: InferenceRuntime,
        cache: SemanticCache | None = None,
        default_constraints: Constraints | None = None,
        fast_models: tuple[str, ...] = (),
        deep_models: tuple[str, ...] = (),
    ) -> None:
        self._router = router
        self._selector = selector
        self._fallback = fallback
        self._runtime = runtime
        self._cache = cache
        # WHY the gateway holds the policy: legacy callers pass a ModelRequest
        # with no Constraints, and the previous adapter built
        # ``Constraints(prefer_local=...)`` from scratch — leaving cost_policy,
        # network_policy and privacy_class at their permissive defaults. Every
        # Zero-Cost-First filter in ModelSelector._passes() was therefore dead
        # on the production path. The active profile's policy now travels with
        # the single egress point, so it cannot be forgotten at a call site.
        self._defaults = default_constraints or Constraints()
        self._fast_models = fast_models
        self._deep_models = deep_models
        self._sources: dict[tuple[str, str], CostClass] = {}

    async def close(self) -> None:
        await self._runtime.close()

    async def health(self) -> dict[str, bool]:
        """Backward compatibility for diagnostics/doctor.py"""
        status: dict[str, bool] = {}
        for p in self._runtime.provider_names():
            status[p] = self._runtime.is_provider_available(p)
        return status

    def _meet_constraints(self, caller: Constraints) -> Constraints:
        """The request may tighten the active profile, never widen its egress."""
        defaults = self._defaults
        update: dict[str, Any] = {
            "privacy_class": _strictest_privacy(defaults.privacy_class, caller.privacy_class),
            "cost_policy": max((defaults.cost_policy, caller.cost_policy), key=_COST_RANK.__getitem__),
            "network_policy": max((defaults.network_policy, caller.network_policy), key=_NETWORK_RANK.__getitem__),
            "prefer_local": defaults.prefer_local or caller.prefer_local,
            "require_streaming": defaults.require_streaming or caller.require_streaming,
        }
        for name in ("max_latency_ms", "max_cost_usd"):
            values = [value for value in (getattr(defaults, name), getattr(caller, name)) if value is not None]
            update[name] = min(values) if values else None
        contexts = [value for value in (defaults.min_context, caller.min_context) if value is not None]
        update["min_context"] = max(contexts) if contexts else None
        if not caller.preferred_models:
            configured = self._deep_models if caller.tier == ModelTier.DEEP else self._fast_models
            update["preferred_models"] = configured or defaults.preferred_models
        if defaults.pinned_model and not caller.pinned_model:
            update["pinned_model"] = defaults.pinned_model
        return caller.model_copy(update=update)

    async def infer(self, req: InferenceRequest) -> InferenceResponse:
        caps = req.required_capabilities
        if any(message.images for message in req.messages):
            caps = caps | {ModelCapability.VISION}
        req = req.model_copy(
            update={"constraints": self._meet_constraints(req.constraints), "required_capabilities": frozenset(caps)}
        )
        required = self._router.required(req)
        ranked = self._selector.select(required, req.constraints)
        sources = {(spec.id, spec.provider): spec.cost_class for spec in ranked}
        self._sources.update(sources)
        # Selection must precede the cache: disabled, unavailable or ineligible
        # models cannot be resurrected by an old response with the same prompt.
        use_cache = self._cache is not None and self._cache.can_cache(req)
        if use_cache and self._cache is not None:
            cached_resp = await self._cache.get(req)
            if cached_resp and (cached_resp.model_id, cached_resp.provider) in sources:
                return cached_resp

        resp = await self._fallback.run(ranked, lambda spec: self._runtime.attempt(req, spec))
        if use_cache and self._cache is not None and not resp.fell_back:
            await self._cache.put(req, resp)
        return resp

    def _constraints_for(self, mr: Any) -> Constraints:
        """Derive selection constraints for a legacy ModelRequest (Phase 4/5).

        Tier preferences come from the request; cost, network and privacy
        restrictions are met with the active profile. Constructing permissive
        defaults here would let an omitted field silently widen egress.
        """
        deep = bool(getattr(mr, "needs_deep_reasoning", False))
        # WHY prefer_local is NOT derived from the tier: the old mapping was
        # `prefer_local = not needs_deep_reasoning`, which made hard reasoning
        # prefer cloud as a side effect of asking for quality — contradicting a
        # local-first profile. Locality is the profile's decision; the tier only
        # expresses the quality/latency trade-off.
        update: dict[str, Any] = {
            "tier": ModelTier.DEEP if deep else ModelTier.FAST,
            "preferred_models": self._deep_models if deep else self._fast_models,
        }
        # A request may declare a stricter privacy class than the profile
        # default; it may never declare a looser one.
        update["cost_policy"] = CostPolicy(getattr(mr, "cost_policy", CostPolicy.UNRESTRICTED))
        update["network_policy"] = NetworkPolicy(getattr(mr, "network_policy", NetworkPolicy.UNRESTRICTED))
        req_privacy = getattr(mr, "privacy_class", None)
        if req_privacy is not None:
            update["privacy_class"] = _strictest_privacy(self._defaults.privacy_class, req_privacy)
        return self._meet_constraints(self._defaults.model_copy(update=update))

    # --- Phase 1-4 compatibility: accept the old ModelRequest shape ---
    async def complete(self, model_request: Any) -> Any:
        """Adapter for existing callers (orchestrator/planner/critique).
        Maps ModelRequest -> InferenceRequest, infers, maps back to ModelResponse.
        WHY: zero churn upstream while the platform underneath is replaced."""
        from atlas.infra.types import ModelResponse, ModelTarget, TokenCost
        from atlas.intelligence.contracts import Message, Role

        mr = model_request  # ModelRequest
        caps = mr.required_capabilities
        if not caps:
            raise ValueError("ModelRequest.required_capabilities must not be empty")
        constraints = self._constraints_for(mr)
        messages = []
        if getattr(mr, "system", None):
            messages.append(Message(role=Role.SYSTEM, content=mr.system))
        messages.append(Message(role=Role.USER, content=mr.prompt, images=getattr(mr, "images", ())))
        req = InferenceRequest(
            correlation_id=mr.correlation_id,
            messages=messages,
            required_capabilities=caps,
            constraints=constraints,
            max_tokens=mr.max_tokens,
            temperature=mr.temperature,
            tools=mr.tools,
        )
        resp = await self.infer(req)
        local = self._sources.get((resp.model_id, resp.provider)) == CostClass.LOCAL
        target = (
            (ModelTarget.LOCAL_HEAVY if constraints.tier == ModelTier.DEEP else ModelTarget.LOCAL_FAST)
            if local
            else ModelTarget.CLOUD
        )
        return ModelResponse(
            text=resp.text,
            target=target,
            model=resp.model_id,
            tool_calls=resp.tool_calls,
            cost=TokenCost(
                input_tokens=resp.usage.input_tokens, output_tokens=resp.usage.output_tokens, usd=resp.usage.usd
            ),
            latency_ms=resp.latency_ms,
            reasoning_details=resp.reasoning_details,
        )
