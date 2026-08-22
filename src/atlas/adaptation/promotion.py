"""Promotion policy and states (Prompt 4 §23-§24, §70).

HARD RULE: SAFETY REGRESSION = REJECT — always, before any other criterion
is consulted. Promotion is also reversible: every promoted strategy version
can be rolled back to its predecessor (§24).

Default mode is SUGGEST_ONLY (§70): the policy computes a decision and the
evidence behind it; applying it is an explicit, audited step.
"""

from __future__ import annotations

import json
from enum import StrEnum

from pydantic import BaseModel, ConfigDict

from atlas.adaptation.domain import (
    ComparisonResult,
    Experiment,
    GeneralizationResult,
    HypothesisStatus,
    PromotionDecision,
    PromotionState,
    StrategyVersion,
)
from atlas.adaptation.hypotheses import HypothesisStore
from atlas.adaptation.strategy_versions import StrategyVersionStore
from atlas.infra.clock import Clock, SystemClock
from atlas.infra.db import Database
from atlas.infra.logging import get_logger

_log = get_logger("atlas.adaptation.promotion")


class PromotionMode(StrEnum):
    """§70: how far the system may go without a human. Default is
    SUGGEST_ONLY. The adaptation system may never modify SafetyEngine."""

    OBSERVE_ONLY = "OBSERVE_ONLY"  # collect telemetry, no decisions
    SUGGEST_ONLY = "SUGGEST_ONLY"  # default — decide, never apply autonomously
    EXPERIMENT_ONLY = "EXPERIMENT_ONLY"  # run experiments, never promote
    HUMAN_APPROVAL = "HUMAN_APPROVAL"  # propose, human applies
    AUTO_WITH_REVIEW = "AUTO_WITH_REVIEW"  # apply, but each apply is human-reviewed
    AUTO_PROMOTE_LOW_RISK = "AUTO_PROMOTE_LOW_RISK"  # auto-apply low-risk only


class PromotionPolicy(BaseModel):
    """§23 thresholds. All are required inputs to a promote."""

    model_config = ConfigDict(frozen=True)

    min_evidence: int = 10  # minimum paired tasks compared
    min_improvement: float = 0.02  # success_rate delta candidate - baseline
    max_cost_increase_ratio: float = 0.25  # candidate may not cost >25% more
    max_latency_increase_ratio: float = 0.25
    min_generalization: float = 0.7  # improvement must survive unseen tasks
    require_safety_no_regression: bool = True
    require_verification: bool = True


def _metric(comparisons: tuple[ComparisonResult, ...], name: str) -> ComparisonResult | None:
    for comparison in comparisons:
        if comparison.metric == name:
            return comparison
    return None


class PromotionManager:
    def __init__(
        self,
        *,
        db: Database,
        hypothesis_store: HypothesisStore,
        version_store: StrategyVersionStore,
        policy: PromotionPolicy | None = None,
        mode: PromotionMode = PromotionMode.SUGGEST_ONLY,
        clock: Clock | None = None,
    ) -> None:
        self._db = db
        self._hypotheses = hypothesis_store
        self._versions = version_store
        self._policy = policy or PromotionPolicy()
        self._mode = mode
        self._clock = clock or SystemClock()

    async def decide(
        self,
        experiment: Experiment,
        comparisons: tuple[ComparisonResult, ...],
        *,
        generalization: GeneralizationResult | None = None,
        safety_regression: bool = False,
        verification_ok: bool = True,
    ) -> PromotionDecision:
        reasons: list[str] = []

        # HARD RULE first, always (§23).
        if safety_regression and self._policy.require_safety_no_regression:
            decision = PromotionDecision(
                experiment_id=experiment.experiment_id,
                hypothesis_id=experiment.hypothesis_id,
                decision="REJECT",
                reasons=("SAFETY REGRESSION = REJECT (hard rule)",),
                safety_regression=True,
                promotion_state=PromotionState.REJECTED,
                created_ts=self._clock.now().isoformat(),
            )
            await self._record(decision)
            await self._hypotheses.set_status(experiment.hypothesis_id, HypothesisStatus.REJECTED)
            return decision

        success = _metric(comparisons, "success_rate")
        cost = _metric(comparisons, "cost_usd")
        latency = _metric(comparisons, "latency_ms")
        evidence = min((c.n for c in comparisons), default=0)

        if evidence < self._policy.min_evidence:
            reasons.append(f"evidence n={evidence} < {self._policy.min_evidence} — HOLD")
        if success is None:
            reasons.append("no success_rate comparison available")
        else:
            delta = success.candidate_mean - success.baseline_mean
            if delta < self._policy.min_improvement:
                reasons.append(f"improvement {delta:.4f} < {self._policy.min_improvement}")
        if cost is not None and cost.baseline_mean > 0:
            increase = (cost.candidate_mean - cost.baseline_mean) / cost.baseline_mean
            if increase > self._policy.max_cost_increase_ratio:
                reasons.append(f"cost increase {increase:.0%} exceeds limit")
        if latency is not None and latency.baseline_mean > 0:
            increase = (latency.candidate_mean - latency.baseline_mean) / latency.baseline_mean
            if increase > self._policy.max_latency_increase_ratio:
                reasons.append(f"latency increase {increase:.0%} exceeds limit")
        if generalization is None:
            reasons.append("no generalization result — improvement not proven on unseen tasks")
        elif generalization.candidate_score < self._policy.min_generalization:
            reasons.append(f"generalization {generalization.candidate_score:.2f} < {self._policy.min_generalization}")
        elif not generalization.holds_on_unseen:
            reasons.append("generalization failed on unseen tasks")
        if self._policy.require_verification and not verification_ok:
            reasons.append("verification requirement not met")

        if reasons:
            hold = any(r.endswith("HOLD") for r in reasons)
            decision = PromotionDecision(
                experiment_id=experiment.experiment_id,
                hypothesis_id=experiment.hypothesis_id,
                decision="HOLD" if hold and len(reasons) == 1 else "REJECT",
                reasons=tuple(reasons),
                promotion_state=PromotionState.REJECTED if not hold else PromotionState.TESTING,
                created_ts=self._clock.now().isoformat(),
            )
            await self._record(decision)
            if decision.decision == "REJECT":
                await self._hypotheses.set_status(experiment.hypothesis_id, HypothesisStatus.REJECTED)
            return decision

        decision = PromotionDecision(
            experiment_id=experiment.experiment_id,
            hypothesis_id=experiment.hypothesis_id,
            decision="PROMOTE",
            reasons=("all policy criteria met",),
            promotion_state=PromotionState.CANDIDATE,
            created_ts=self._clock.now().isoformat(),
        )
        await self._record(decision)
        await self._hypotheses.set_status(experiment.hypothesis_id, HypothesisStatus.PROMOTABLE)
        _log.info(
            "promotion.decided",
            event_type="adaptation",
            experiment_id=experiment.experiment_id,
            decision="PROMOTE",
        )
        return decision

    async def apply(
        self,
        decision: PromotionDecision,
        *,
        strategy_id: str,
        definition: str,
        change_reason: str,
        approved_by: str = "human",
    ) -> PromotionDecision:
        """Explicit, audited apply: cut a new immutable StrategyVersion and mark
        the hypothesis PROMOTED. Never called autonomously in SUGGEST_ONLY."""
        version_number = await self._versions.next_version(strategy_id)
        version = StrategyVersion(
            strategy_id=strategy_id,
            version=version_number,
            definition=definition,
            change_reason=change_reason,
            source_experiments=(decision.experiment_id,),
            created_ts=self._clock.now().isoformat(),
        )
        await self._versions.save_version(version)
        applied = PromotionDecision(
            experiment_id=decision.experiment_id,
            hypothesis_id=decision.hypothesis_id,
            decision="PROMOTE",
            reasons=(*decision.reasons, f"applied by {approved_by}"),
            promotion_state=PromotionState.PROMOTED,
            promoted_strategy_id=strategy_id,
            promoted_version=version_number,
            created_ts=self._clock.now().isoformat(),
        )
        await self._record(applied)
        await self._hypotheses.set_status(decision.hypothesis_id, HypothesisStatus.PROMOTED)
        await self._event("strategy_promoted", strategy_id, {"version": version_number})
        return applied

    async def rollback(self, strategy_id: str, *, reason: str) -> PromotionDecision | None:
        """§24: restore the previous version. The promoted row is marked
        ROLLED_BACK; the live surface should select `previous_version`."""
        cur = await self._db.conn.execute(
            "SELECT * FROM promotion_decisions WHERE promoted_strategy_id=? AND promotion_state='PROMOTED'"
            " ORDER BY id DESC LIMIT 1",
            (strategy_id,),
        )
        row = await cur.fetchone()
        if row is None:
            return None
        d = dict(row)
        previous = (d["promoted_version"] or 1) - 1
        decision = PromotionDecision(
            experiment_id=d["experiment_id"],
            hypothesis_id=d["hypothesis_id"],
            decision="REJECT",
            reasons=(f"ROLLBACK: {reason}",),
            promotion_state=PromotionState.ROLLED_BACK,
            promoted_strategy_id=strategy_id,
            promoted_version=max(previous, 1),
            created_ts=self._clock.now().isoformat(),
        )
        await self._record(decision)
        await self._hypotheses.set_status(d["hypothesis_id"], HypothesisStatus.ROLLED_BACK)
        await self._db.conn.execute(
            "UPDATE promotion_decisions SET promotion_state='ROLLED_BACK' WHERE id=?", (d["id"],)
        )
        await self._db.conn.commit()
        await self._event(
            "strategy_rolled_back",
            strategy_id,
            {"from_version": d["promoted_version"], "to_version": decision.promoted_version, "reason": reason},
        )
        _log.warning(
            "strategy.rolled_back",
            event_type="adaptation",
            strategy_id=strategy_id,
            reason=reason,
        )
        return decision

    async def decisions_for(self, experiment_id: str) -> tuple[PromotionDecision, ...]:
        cur = await self._db.conn.execute(
            "SELECT * FROM promotion_decisions WHERE experiment_id=? ORDER BY id", (experiment_id,)
        )
        rows = await cur.fetchall()
        result: list[PromotionDecision] = []
        for row in rows:
            d = dict(row)
            result.append(
                PromotionDecision(
                    experiment_id=d["experiment_id"],
                    hypothesis_id=d["hypothesis_id"],
                    decision=d["decision"],
                    reasons=tuple(json.loads(d["reasons_json"])),
                    safety_regression=bool(d["safety_regression"]),
                    promotion_state=PromotionState(d["promotion_state"]),
                    promoted_strategy_id=d["promoted_strategy_id"],
                    promoted_version=d["promoted_version"],
                    created_ts=d["created_ts"],
                )
            )
        return tuple(result)

    async def _record(self, decision: PromotionDecision) -> None:
        await self._db.conn.execute(
            """
            INSERT INTO promotion_decisions (
                experiment_id, hypothesis_id, decision, reasons_json,
                safety_regression, promotion_state, promoted_strategy_id,
                promoted_version, created_ts
            ) VALUES (?,?,?,?,?,?,?,?,?)
            """,
            (
                decision.experiment_id,
                decision.hypothesis_id,
                decision.decision,
                json.dumps(list(decision.reasons)),
                int(decision.safety_regression),
                decision.promotion_state.value,
                decision.promoted_strategy_id,
                decision.promoted_version,
                decision.created_ts,
            ),
        )
        await self._db.conn.commit()

    async def _event(self, kind: str, ref_id: str, detail: dict[str, object]) -> None:
        await self._db.conn.execute(
            "INSERT INTO adaptation_events (ts, kind, ref_id, detail_json) VALUES (?,?,?,?)",
            (self._clock.now().isoformat(), kind, ref_id, json.dumps(detail)),
        )
        await self._db.conn.commit()


__all__ = ["PromotionManager", "PromotionMode", "PromotionPolicy"]
