"""Hypothesis generation and lifecycle (Prompt 4 §16-§18, §93).

"Do not generate hypotheses from one noisy event." A hypothesis is only
proposed from REPEATED measured evidence (failure clusters ≥3, strategy
underperformance, benchmark regressions) — never from a single occurrence.

§93 falsification: a hypothesis is REJECTED when its experiment shows no
improvement, the evidence falls below threshold, a safety regression occurs,
or generalization fails. Rejection is stored as negative knowledge (§89).
"""

from __future__ import annotations

import json

from atlas.adaptation.clustering import FailureCluster
from atlas.adaptation.domain import (
    MIN_EVIDENCE_DEFAULT,
    AllowedChangeType,
    Hypothesis,
    HypothesisStatus,
)
from atlas.adaptation.taxonomy import FailureClass
from atlas.infra.clock import Clock, SystemClock
from atlas.infra.db import Database
from atlas.infra.logging import get_logger

_log = get_logger("atlas.adaptation.hypotheses")

# Evidence thresholds (§16): default 3; higher-risk change types need more.
HIGH_RISK_CHANGE_TYPES: frozenset[AllowedChangeType] = frozenset(
    {
        AllowedChangeType.MODEL_ROUTING,
        AllowedChangeType.WORKFLOW_ORDERING,
        AllowedChangeType.CONTEXT_COMPILATION,
    }
)
MIN_EVIDENCE_HIGH_RISK = 5

# Failure class → the allowed change type that could fix it (§18 mapping).
_CHANGE_TYPE_BY_CLASS: dict[FailureClass, AllowedChangeType | None] = {
    FailureClass.INTENT_FAILURE: AllowedChangeType.STRATEGY,
    FailureClass.PLANNING_FAILURE: AllowedChangeType.STRATEGY,
    FailureClass.REASONING_FAILURE: AllowedChangeType.MODEL_ROUTING,
    FailureClass.CAPABILITY_SELECTION_FAILURE: AllowedChangeType.STRATEGY,
    FailureClass.TOOL_SELECTION_FAILURE: AllowedChangeType.TOOL_RANKING,
    FailureClass.TOOL_EXECUTION_FAILURE: AllowedChangeType.TOOL_RANKING,
    FailureClass.PERCEPTION_FAILURE: AllowedChangeType.STRATEGY,
    FailureClass.TARGET_GROUNDING_FAILURE: AllowedChangeType.STRATEGY,
    FailureClass.RETRIEVAL_FAILURE: AllowedChangeType.QUERY_REWRITING,
    FailureClass.RERANK_FAILURE: AllowedChangeType.RERANKER_CHOICE,
    FailureClass.KNOWLEDGE_FAILURE: AllowedChangeType.SOURCE_PREFERENCE,
    FailureClass.MEMORY_FAILURE: AllowedChangeType.SOURCE_PREFERENCE,
    FailureClass.MODEL_FAILURE: AllowedChangeType.MODEL_ROUTING,
    FailureClass.CONTEXT_FAILURE: AllowedChangeType.CONTEXT_COMPILATION,
    FailureClass.VERIFICATION_FAILURE: AllowedChangeType.VERIFICATION_ORDERING,
    FailureClass.RECOVERY_FAILURE: AllowedChangeType.STRATEGY,
    FailureClass.RESOURCE_FAILURE: AllowedChangeType.WORKFLOW_ORDERING,
    FailureClass.TIMEOUT: AllowedChangeType.WORKFLOW_ORDERING,
    FailureClass.BUDGET_FAILURE: AllowedChangeType.STRATEGY,
    # Never generate learning hypotheses for these — they are policy or
    # environment outcomes, not adaptation targets:
    FailureClass.SAFETY_BLOCK: None,
    FailureClass.AUTH_FAILURE: None,
    FailureClass.USER_CONSTRAINT_FAILURE: None,
    FailureClass.ENVIRONMENT_FAILURE: None,
}


class HypothesisGenerator:
    """Turns repeated measured evidence into testable hypotheses."""

    def from_failure_cluster(self, cluster: FailureCluster) -> Hypothesis | None:
        failure_class = FailureClass(cluster.key.failure_class)
        change_type = _CHANGE_TYPE_BY_CLASS[failure_class]
        if change_type is None:
            return None  # policy/environment outcome — not learnable
        threshold = MIN_EVIDENCE_HIGH_RISK if change_type in HIGH_RISK_CHANGE_TYPES else MIN_EVIDENCE_DEFAULT
        if cluster.count < threshold:
            return None  # §16: not enough repeated evidence yet

        scope = cluster.key.component or cluster.key.task_class or "runtime"
        return Hypothesis(
            title=f"reduce {failure_class.value} in {scope}",
            problem_statement=(
                f"{cluster.count} trajectories failed with {failure_class.value}"
                f" (component={cluster.key.component or 'n/a'},"
                f" model={cluster.key.model or 'n/a'}, task={cluster.key.task_class or 'n/a'})"
                f" between {cluster.first_seen_ts} and {cluster.last_seen_ts}"
            ),
            evidence=tuple(cluster.trajectory_ids),
            affected_component=scope,
            proposed_change=f"adjust {change_type.value.lower().replace('_', ' ')} for this failure pattern",
            change_type=change_type,
            expected_effect=f"fewer {failure_class.value} occurrences in the affected scope",
            risk="MEDIUM" if change_type in HIGH_RISK_CHANGE_TYPES else "LOW",
            evaluation_plan=(
                f"offline baseline-vs-candidate benchmark on the {failure_class.value} task set;"
                " compare success_rate, quality_score, latency_ms, cost_usd; check generalization"
            ),
        )

    def from_strategy_underperformance(
        self,
        strategy_id: str,
        version: int,
        *,
        runs: int,
        success_rate: float,
        threshold: float = 0.5,
    ) -> Hypothesis | None:
        """§16 source: strategy underperformance with enough runs."""
        if runs < MIN_EVIDENCE_DEFAULT or success_rate >= threshold:
            return None
        return Hypothesis(
            title=f"improve underperforming strategy {strategy_id} v{version}",
            problem_statement=(
                f"strategy {strategy_id} v{version} success rate {success_rate:.2f}"
                f" over {runs} runs is below {threshold}"
            ),
            evidence=(f"strategy:{strategy_id}:v{version}",),
            affected_component=strategy_id,
            proposed_change="create a candidate strategy version with a revised approach",
            change_type=AllowedChangeType.STRATEGY,
            expected_effect="success rate restored above threshold without cost regression",
            risk="LOW",
            evaluation_plan="paired offline benchmark against the current version",
        )


class HypothesisStore:
    def __init__(self, *, db: Database, clock: Clock | None = None) -> None:
        self._db = db
        self._clock = clock or SystemClock()

    async def save(self, hypothesis: Hypothesis) -> None:
        await self._db.conn.execute(
            """
            INSERT OR REPLACE INTO hypotheses (
                hypothesis_id, title, problem_statement, evidence_json,
                affected_component, proposed_change, change_type, expected_effect,
                risk, constraints_json, evaluation_plan, status, experiment_id,
                created_ts, updated_ts
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                hypothesis.hypothesis_id,
                hypothesis.title,
                hypothesis.problem_statement,
                json.dumps(list(hypothesis.evidence)),
                hypothesis.affected_component,
                hypothesis.proposed_change,
                hypothesis.change_type.value,
                hypothesis.expected_effect,
                hypothesis.risk,
                json.dumps(list(hypothesis.constraints)),
                hypothesis.evaluation_plan,
                hypothesis.status.value,
                hypothesis.experiment_id,
                hypothesis.created_ts,
                hypothesis.updated_ts,
            ),
        )
        await self._db.conn.commit()

    async def get(self, hypothesis_id: str) -> Hypothesis | None:
        cur = await self._db.conn.execute("SELECT * FROM hypotheses WHERE hypothesis_id=?", (hypothesis_id,))
        row = await cur.fetchone()
        return _from_row(row) if row is not None else None

    async def by_status(self, status: HypothesisStatus) -> tuple[Hypothesis, ...]:
        cur = await self._db.conn.execute(
            "SELECT * FROM hypotheses WHERE status=? ORDER BY updated_ts", (status.value,)
        )
        rows = await cur.fetchall()
        return tuple(h for r in rows if (h := _from_row(r)) is not None)

    async def all(self) -> tuple[Hypothesis, ...]:
        cur = await self._db.conn.execute("SELECT * FROM hypotheses ORDER BY updated_ts DESC")
        rows = await cur.fetchall()
        return tuple(h for r in rows if (h := _from_row(r)) is not None)

    async def set_status(
        self,
        hypothesis_id: str,
        status: HypothesisStatus,
        *,
        experiment_id: str | None = None,
    ) -> None:
        if experiment_id is not None:
            await self._db.conn.execute(
                "UPDATE hypotheses SET status=?, experiment_id=?, updated_ts=? WHERE hypothesis_id=?",
                (status.value, experiment_id, self._clock.now().isoformat(), hypothesis_id),
            )
        else:
            await self._db.conn.execute(
                "UPDATE hypotheses SET status=?, updated_ts=? WHERE hypothesis_id=?",
                (status.value, self._clock.now().isoformat(), hypothesis_id),
            )
        await self._db.conn.commit()

    async def exists_for_component(self, affected_component: str) -> bool:
        """Deduplicate: one open hypothesis per affected component."""
        cur = await self._db.conn.execute(
            "SELECT 1 FROM hypotheses WHERE affected_component=? AND status IN"
            " ('PROPOSED','QUEUED','RUNNING','EVALUATED','PROMOTABLE') LIMIT 1",
            (affected_component,),
        )
        return await cur.fetchone() is not None


def _from_row(row: object) -> Hypothesis | None:
    if row is None:
        return None
    d = dict(row)
    return Hypothesis(
        hypothesis_id=d["hypothesis_id"],
        title=d["title"],
        problem_statement=d["problem_statement"],
        evidence=tuple(json.loads(d["evidence_json"])),
        affected_component=d["affected_component"],
        proposed_change=d["proposed_change"],
        change_type=AllowedChangeType(d["change_type"]),
        expected_effect=d["expected_effect"],
        risk=d["risk"],
        constraints=tuple(json.loads(d["constraints_json"])),
        evaluation_plan=d["evaluation_plan"],
        status=HypothesisStatus(d["status"]),
        experiment_id=d["experiment_id"],
        created_ts=d["created_ts"],
        updated_ts=d["updated_ts"],
    )


__all__ = [
    "MIN_EVIDENCE_HIGH_RISK",
    "HypothesisGenerator",
    "HypothesisStore",
]
