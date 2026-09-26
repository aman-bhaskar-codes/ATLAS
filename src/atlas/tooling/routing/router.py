"""RoutingEngine — the L0-L9 control plane (Part 3 §11/§45/§90).

One engine, layered contracts, no 1,000-line god class: each layer is a method
with a typed input/output and a structured reason it appends to the decision
(§68). Not every task reaches every layer — the cascade short-circuits on the
deterministic fast path (§45/§46).

Hard invariants enforced here (also property-tested, §84):
* the hard filter runs BEFORE any judgment provider (§20);
* judgment is a signal, never authorization (§31);
* routing never executes — the plan references candidates that later flow
  through the existing governed funnels (ToolingExecutor → SafetyEngine, §21);
* identical TaskIR + catalog + policy + judgment → identical route (§66).
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from atlas.infra.logging import get_logger
from atlas.tooling.catalog.catalog import ToolCatalog
from atlas.tooling.routing.candidates import CandidateDiscovery, HardFilter
from atlas.tooling.routing.config import RoutingCfg
from atlas.tooling.routing.domains import DomainRegistry
from atlas.tooling.routing.judgment import (
    JudgmentCascade,
    JudgmentQuestion,
    JudgmentState,
)
from atlas.tooling.routing.models import (
    Complexity,
    ConfidenceLevel,
    DecisionRisk,
    JudgmentBatch,
    RejectedCandidate,
    RouteCandidate,
    RouteDecision,
    RouteGraph,
    RoutePlan,
    TaskIR,
)
from atlas.tooling.routing.normalize import TaskNormalizer
from atlas.tooling.routing.planner import RoutePlanner, RoutePlanValidationError
from atlas.tooling.routing.scoring import compose_final_score, deterministic_score, rank
from atlas.tooling.routing.store import RouteStore
from atlas.tooling.routing.strategies import StrategyRegistry

_log = get_logger("atlas.tooling.routing.engine")


@dataclass(frozen=True)
class RoutingResult:
    decision: RouteDecision
    plan: RoutePlan | None
    graph: RouteGraph | None


class RoutingEngine:
    def __init__(
        self,
        *,
        config: RoutingCfg,
        domains: DomainRegistry,
        strategies: StrategyRegistry,
        catalog: ToolCatalog | None,
        cascade: JudgmentCascade,
        store: RouteStore | None,
        publish: Any = None,
        normalizer: TaskNormalizer | None = None,
    ) -> None:
        self._config = config
        self._domains = domains
        self._strategies = strategies
        self._catalog = catalog
        self._cascade = cascade
        self._store = store
        self._publish = publish
        self._normalizer = normalizer
        self._discovery = CandidateDiscovery(catalog)
        self._filter = HardFilter()
        self._planner = RoutePlanner()

    def normalize_request(
        self,
        *,
        objective: str,
        task_id: str,
        correlation_id: str,
        source: str = "api",
    ) -> TaskIR:
        """L0 public entry: any ingress text -> TaskIR (§12)."""
        if self._normalizer is None:
            raise RuntimeError("routing engine has no TaskNormalizer; bootstrap must inject one")
        return self._normalizer.normalize(
            objective=objective,
            task_id=task_id,
            correlation_id=correlation_id,
            source=source,
        )

    # ── Public API (§69) ──────────────────────────────────────────── #

    async def route(
        self,
        task: TaskIR,
        *,
        capability: str | None = None,
        operation: str | None = None,
    ) -> RoutingResult:
        """Full L0-L9 pass. Never executes anything — produces a decision +
        plan + graph (§92)."""
        started = time.perf_counter()
        route_id = f"route-{uuid.uuid4().hex[:12]}"
        reasons: list[str] = []
        await self._emit("route.started", {"route_id": route_id, "task_id": task.task_id})

        # L1: domain routing (cascade inside).
        domain_id, domain_reasons, domain_certainty, judgment_batch = await self._route_domain(task)
        reasons.extend(domain_reasons)
        await self._emit("route.domain_selected", {"route_id": route_id, "domain": domain_id, "task_id": task.task_id})

        if domain_id is None:
            # §64: unresolved domain → ask the human, do not guess.
            decision = self._decision(
                route_id, task, decision_type="ask_user", reasons=reasons, judgment=judgment_batch
            )
            finalized = await self._finalize(decision, None, None, started)
            return RoutingResult(decision=finalized, plan=None, graph=None)

        self._domains.get(domain_id)  # fail fast if the domain vanished mid-route

        # L2: strategy routing (deterministic map over domain declarations).
        strategy = self._route_strategy(task, domain_id)
        reasons.append(f"strategy {strategy} selected for complexity={task.complexity.value} in domain {domain_id}")
        await self._emit("route.strategy_selected", {"route_id": route_id, "strategy": strategy})

        # L3: capability decomposition — validated against real registrations (§16).
        capabilities = self._decompose_capabilities(task, domain_id)
        reasons.append(f"capabilities resolved: {', '.join(capabilities) or '(none)'}")
        await self._emit("route.capabilities_resolved", {"route_id": route_id, "capabilities": list(capabilities)})

        # L4: candidate discovery (catalog first; §18).
        candidates: list[RouteCandidate] = list(self._discovery.discover_all(task))
        await self._emit("route.candidates_discovered", {"route_id": route_id, "count": len(candidates)})

        # L5: hard policy filter — BEFORE judgment, always (§20).
        filtered, rejected = self._filter.apply(
            candidates,
            task,
            required_operation=operation,
        )
        reasons.append(
            f"hard filter: {len(filtered)} eligible, {len(rejected)} rejected "
            f"({', '.join(sorted({r.reason for r in rejected})) or 'none'})"
        )
        await self._emit(
            "route.candidates_filtered",
            {"route_id": route_id, "eligible": len(filtered), "rejected": len(rejected)},
        )

        if not filtered:
            decision = self._decision(
                route_id,
                task,
                decision_type="no_route",
                domain=domain_id,
                strategy=strategy,
                candidates=tuple(candidates),
                rejected=tuple(rejected),
                reasons=[*reasons, "no policy-eligible candidate exists"],
                judgment=judgment_batch,
                deterministic_certainty=domain_certainty,
            )
            finalized = await self._finalize(decision, None, None, started)
            return RoutingResult(decision=finalized, plan=None, graph=None)

        # L6: bounded judgment over the top-N survivors only (§27/§28).
        top_n = filtered[: self._config.judgment_top_n]
        judgment_scores = await self._judge_candidates(task, top_n, judgment_batch)

        # L7: deterministic ranking + bounded judgment composition (§33/§34).
        scored = [
            compose_final_score(
                deterministic_score(c, task, required_capability=capability, required_operation=operation),
                judgment_scores.get(c.candidate_id),
                self._config.weights,
            )
            for c in filtered
        ]
        ordered = rank(scored)
        ranking_scores = {s.candidate.candidate_id: s.total for s in ordered}
        score_components = {s.candidate.candidate_id: s.components for s in ordered}
        reasons.append(f"ranking: {ordered[0].candidate.candidate_id} leads at {ordered[0].total:.3f}")

        # L8: compile the plan + execution graph, validate, reject invalid (§65).
        decision = self._decision(
            route_id,
            task,
            decision_type="route",
            domain=domain_id,
            strategy=strategy,
            candidates=tuple(candidates),
            filtered_candidates=tuple(c.candidate_id for c in filtered),
            rejected=tuple(rejected),
            reasons=reasons,
            judgment=judgment_batch,
            ranking_scores=ranking_scores,
            score_components=score_components,
            selected=ordered[0].candidate.candidate_id,
            fallbacks=tuple(s.candidate.candidate_id for s in ordered[1:4]),
            deterministic_certainty=domain_certainty,
            judgment_confidence=self._overall_judgment_confidence(judgment_batch),
        )
        try:
            plan = self._planner.compile(decision, capability=capability, operation=operation)
            graph = self._planner.build_graph(plan, decision)
            known = {c.candidate_id: c for c in candidates}
            self._planner.require_valid(plan, graph, known)
        except RoutePlanValidationError as exc:
            _log.error("route.plan.invalid", event_type="routing", route_id=route_id, error=str(exc))
            decision = decision.model_copy(
                update={
                    "decision_type": "no_route",
                    "reasons": (*decision.reasons, f"plan validation failed: {exc}"),
                }
            )
            await self._finalize(decision, None, None, started)
            return RoutingResult(decision=decision, plan=None, graph=None)

        await self._emit(
            "route.compiled",
            {"route_id": route_id, "plan_id": plan.plan_id, "selected": decision.selected_candidate},
        )
        finalized = await self._finalize(decision, plan, graph, started)
        return RoutingResult(decision=finalized, plan=plan, graph=graph)

    # ── L1: domain routing cascade (§13/§14) ──────────────────────── #

    async def _route_domain(self, task: TaskIR) -> tuple[str | None, list[str], float, JudgmentBatch]:
        reasons: list[str] = []
        hint = TaskNormalizer.fast_path(task.objective)
        if hint is not None:
            try:
                self._domains.require_available(hint.domain)
                reasons.append(f"domain {hint.domain} via deterministic fast-path rule {hint.rule_id}")
                return hint.domain, reasons, hint.score, self._empty_batch()
            except Exception:
                reasons.append(f"fast-path rule {hint.rule_id} matched {hint.domain} but it is unavailable")

        available = self._domains.available()
        if not available:
            reasons.append("no domains available")
            return None, reasons, 0.0, self._empty_batch()

        # Tier 1: bounded judgment (§45) — only because rules were inconclusive.
        options = tuple(d.id for d in available)
        batch = await self._cascade.ask(
            (
                JudgmentQuestion(
                    question_id="domain",
                    text="Which domain best matches this task?",
                    kind="choice",
                    options=options,
                ),
            ),
            JudgmentState(
                state_id=task.correlation_id,
                summary=f"objective: {task.objective}\ncomplexity: {task.complexity.value}\nsource: {task.source}",
                data={"objective": task.objective, "complexity": task.complexity.value},
            ),
            risk=DecisionRisk.LOW_RISK_ROUTING,
        )
        domain_result = batch.results[0] if batch.results else None
        if domain_result is not None and domain_result.accepted and domain_result.choice in options:
            reasons.append(
                f"domain {domain_result.choice} via {domain_result.provider} judgment "
                f"(score {domain_result.score:.2f}, {domain_result.confidence.value})"
            )
            return domain_result.choice, reasons, 0.0, batch

        # Low-confidence → safe default: the general domain (never a guess at an
        # unavailable specialist). Explicitly recorded (§30).
        if "general" in options:
            reasons.append(
                f"domain judgment unaccepted (provider={domain_result.provider if domain_result else 'none'}) "
                "→ safe default 'general'"
            )
            return "general", reasons, 0.0, batch
        reasons.append("domain unresolved → ask_user (§64)")
        return None, reasons, 0.0, batch

    # ── L2: strategy routing (§15) ────────────────────────────────── #

    def _route_strategy(self, task: TaskIR, domain_id: str) -> str:
        domain = self._domains.get(domain_id)
        supported = [s for s in domain.supported_strategies if self._strategy_exists(s)]
        if not supported:
            return "SINGLE_AGENT"
        if task.complexity == Complexity.SIMPLE and "DIRECT" in supported:
            return "DIRECT"
        # Domain-primary strategy first (research → RESEARCH, ide → SOFTWARE_ENGINEERING).
        for preferred in ("RESEARCH", "SOFTWARE_ENGINEERING"):
            if preferred in supported and task.complexity != Complexity.SIMPLE:
                return preferred
        for shape_preference in ("ITERATIVE", "DAG", "PARALLEL", "SEQUENTIAL", "SINGLE_AGENT"):
            if shape_preference in supported and task.complexity == Complexity.COMPLEX:
                return shape_preference
        return supported[0]

    def _strategy_exists(self, strategy_id: str) -> bool:
        try:
            self._strategies.get(strategy_id)
        except Exception:
            return False
        return True

    # ── L3: capability decomposition (§16) ────────────────────────── #

    def _decompose_capabilities(self, task: TaskIR, domain_id: str) -> tuple[str, ...]:
        """Deterministic keyword decomposition, VALIDATED against the domain's
        declared capabilities — invented capabilities are dropped (§16)."""
        domain = self._domains.get(domain_id)
        text = task.objective.lower()
        found: list[str] = []
        keyword_map = {
            "web_search": ("search", "web", "latest", "news"),
            "academic_search": ("paper", "papers", "arxiv", "academic", "literature"),
            "document_fetch": ("fetch", "read_url", "retrieve", "download"),
            "comparison": ("compare", "versus", "vs"),
            "synthesis": ("summarize", "synthes", "overview"),
            "citation": ("cite", "citation", "source"),
            "repository_inspection": ("repository", "repo", "inspect", "codebase"),
            "editing": ("edit", "fix", "implement", "refactor", "modify"),
            "test_execution": ("test", "pytest", "run tests"),
            "filesystem": ("file", "directory", "readme"),
            "shell": ("command", "script", "shell"),
        }
        for capability, keywords in keyword_map.items():
            if capability in domain.capabilities and any(k in text for k in keywords):
                found.append(capability)
        if not found:
            return (domain.capabilities[0],) if domain.capabilities else ()
        return tuple(found)

    # ── L6: candidate judgment (§27) ──────────────────────────────── #

    async def _judge_candidates(
        self,
        task: TaskIR,
        top_n: list[RouteCandidate],
        domain_batch: JudgmentBatch,
    ) -> dict[str, float]:
        """Bounded judgment: only when a provider accepted the DOMAIN judgment
        do we spend judgment on candidates — and only on the top-N set.
        Returns candidate_id → judgment score (0..1); empty when no judgment
        provider is active (deterministic-only routing, §61/§88)."""
        if not self._config.enable_jev and not self._config.enable_llm_judgment:
            return {}
        if all(r.provider == "deterministic" for r in domain_batch.results):
            return {}
        questions = tuple(
            JudgmentQuestion(
                question_id=f"candidate:{c.candidate_id}",
                text=f"Is {c.display_name or c.candidate_id} a good fit for this task?",
                kind="score",
            )
            for c in top_n
        )
        batch = await self._cascade.ask(
            questions,
            JudgmentState(
                state_id=task.correlation_id,
                summary=f"objective: {task.objective}",
                data={"candidates": [c.candidate_id for c in top_n]},
            ),
            risk=DecisionRisk.LOW_RISK_ROUTING,
        )
        return {r.question_id.split(":", 1)[1]: r.score for r in batch.results if r.score is not None}

    # ── Decision assembly + persistence (§35/§55) ─────────────────── #

    def _decision(
        self,
        route_id: str,
        task: TaskIR,
        *,
        decision_type: str,
        reasons: list[str],
        judgment: JudgmentBatch,
        domain: str = "",
        strategy: str = "",
        candidates: tuple[RouteCandidate, ...] = (),
        filtered_candidates: tuple[str, ...] = (),
        rejected: tuple[RejectedCandidate, ...] = (),
        ranking_scores: dict[str, float] | None = None,
        score_components: dict[str, dict[str, float]] | None = None,
        selected: str | None = None,
        fallbacks: tuple[str, ...] = (),
        deterministic_certainty: float = 0.0,
        judgment_confidence: float | None = None,
    ) -> RouteDecision:
        overall = deterministic_certainty if selected else 0.0
        if selected and judgment_confidence is not None:
            overall = max(overall, judgment_confidence)
        elif selected and deterministic_certainty == 0.0:
            # deterministic-only route over live candidates: certainty from
            # the deterministic score, recorded per-component (§63).
            overall = (ranking_scores or {}).get(selected, 0.0)
        level = (
            ConfidenceLevel.HIGH
            if overall >= 0.85
            else ConfidenceLevel.MEDIUM
            if overall >= 0.60
            else ConfidenceLevel.LOW
        )
        return RouteDecision(
            route_id=route_id,
            request_id=task.task_id,
            correlation_id=task.correlation_id,
            objective=task.objective,
            decision_type=decision_type,
            domain=domain,
            strategy=strategy,
            candidates=candidates,
            filtered_candidates=filtered_candidates,
            rejected=tuple(rejected),
            judgment=judgment,
            ranking_scores=ranking_scores or {},
            score_components=score_components or {},
            selected_candidate=selected,
            fallback_candidates=fallbacks,
            deterministic_certainty=deterministic_certainty,
            judgment_confidence=judgment_confidence,
            judgment_provider=(
                judgment.results[0].provider if judgment.results and judgment.results[0].provider != "none" else None
            ),
            overall_confidence=round(overall, 4),
            confidence_level=level,
            reason=reasons[-1] if reasons else "",
            reasons=tuple(reasons),
            policy_snapshot={
                "network_policy": task.network_policy.value,
                "cost_policy": task.cost_policy.value,
                "privacy_class": task.privacy_class.value,
                "enable_jev": self._config.enable_jev,
                "enable_llm_judgment": self._config.enable_llm_judgment,
            },
            catalog_version=self._catalog.version() if self._catalog else 0,
            created_at=None,
        )

    def _empty_batch(self) -> JudgmentBatch:
        return JudgmentBatch()

    @staticmethod
    def _overall_judgment_confidence(batch: JudgmentBatch) -> float | None:
        for result in batch.results:
            if result.provider in ("jev", "llm") and result.score is not None:
                return float(result.score)
        return None

    async def _finalize(
        self,
        decision: RouteDecision,
        plan: RoutePlan | None,
        graph: RouteGraph | None,
        started: float,
    ) -> RouteDecision:
        finished = decision.model_copy(
            update={
                "duration_ms": int((time.perf_counter() - started) * 1000),
                "created_at": datetime.now(UTC),
            }
        )
        if self._store is not None:
            try:
                await self._store.save(decision=finished, plan=plan, graph=graph)
            except Exception as exc:
                _log.error("route.persist.failed", event_type="routing", route_id=decision.route_id, error=repr(exc))
        kind = "route.completed" if finished.decision_type == "route" else "route.failed"
        await self._emit(kind, {"route_id": finished.route_id, "decision_type": finished.decision_type})
        _log.info(
            "route.decision",
            event_type="routing",
            route_id=finished.route_id,
            decision_type=finished.decision_type,
            domain=finished.domain,
            strategy=finished.strategy,
            selected=finished.selected_candidate,
            confidence=finished.overall_confidence,
            duration_ms=finished.duration_ms,
        )
        return finished

    async def _emit(self, kind: str, payload: dict[str, Any]) -> None:
        if self._publish is not None:
            try:
                await self._publish(kind, payload)
            except Exception as exc:
                _log.warning("route.event.failed", event_type="routing", kind=kind, error=repr(exc))

    # ── Service surface (§69) ─────────────────────────────────────── #

    def list_domains(self) -> list[dict[str, object]]:
        return [
            {
                "id": d.id,
                "display_name": d.display_name,
                "description": d.description,
                "capabilities": list(d.capabilities),
                "supported_strategies": list(d.supported_strategies),
                "available": d.is_available(),
            }
            for d in self._domains.list()
        ]

    def list_strategies(self) -> list[dict[str, object]]:
        return [
            {
                "strategy_id": s.strategy_id,
                "description": s.description,
                "execution_shape": s.execution_shape,
                "supports_parallelism": s.supports_parallelism,
                "supports_replanning": s.supports_replanning,
                "template": s.template,
            }
            for s in self._strategies.list()
        ]

    async def inspect_route(self, route_id: str) -> dict[str, Any] | None:
        if self._store is None:
            return None
        return await self._store.load(route_id)

    async def explain(self, route_id: str) -> dict[str, object] | None:
        """Structured route explanation (§68): per-layer reasons, judgments,
        score components, rejections — not a generated paragraph."""
        payload = await self.inspect_route(route_id)
        if payload is None:
            return None
        decision: dict[str, Any] = dict(payload["decision"])
        judgment_data: dict[str, Any] = dict(decision["judgment"])
        return {
            "route_id": decision["route_id"],
            "reasons": list(decision["reasons"]),
            "domain": decision["domain"],
            "strategy": decision["strategy"],
            "selected_candidate": decision["selected_candidate"],
            "fallback_candidates": list(decision["fallback_candidates"]),
            "rejected": [
                {"candidate_id": r["candidate_id"], "reason": r["reason"], "stage": r["stage"]}
                for r in decision["rejected"]
            ],
            "judgment": {
                "provider_chain": list(judgment_data["provider_chain"]),
                "results": [
                    {
                        "question_id": r["question_id"],
                        "provider": r["provider"],
                        "choice": r["choice"],
                        "score": r["score"],
                        "confidence": r["confidence"],
                        "accepted": r["accepted"],
                    }
                    for r in judgment_data["results"]
                ],
            },
            "score_components": decision["score_components"],
            "confidence": {
                "deterministic_certainty": decision["deterministic_certainty"],
                "judgment_confidence": decision["judgment_confidence"],
                "judgment_provider": decision["judgment_provider"],
                "overall": decision["overall_confidence"],
                "level": decision["confidence_level"],
            },
            "catalog_version": decision["catalog_version"],
        }

    async def recent_routes(self, limit: int = 20) -> list[dict[str, object]]:
        if self._store is None:
            return []
        return await self._store.recent(limit)

    # ── Replay (§67) ──────────────────────────────────────────────── #

    async def replay(self, route_id: str) -> RoutingResult | None:
        """Rebuild a decision from the RECORDED snapshot — no fresh external
        calls (§67). Judgment and candidates come from the store."""
        if self._store is None:
            return None
        payload = await self._store.load(route_id)
        if payload is None:
            return None

        decision = RouteDecision.model_validate(payload["decision"])
        plan = RoutePlan.model_validate(payload["plan"]) if payload.get("plan") else None
        graph = RouteGraph.model_validate(payload["graph"]) if payload.get("graph") else None
        replayed = decision.model_copy(
            update={"reasons": (*decision.reasons, "REPLAY: reconstructed from recorded snapshot (§67)")}
        )
        return RoutingResult(decision=replayed, plan=plan, graph=graph)
