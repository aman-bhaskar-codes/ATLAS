"""Decision quality — retrospective process evaluation (Prompt 4 §31).

ProcessEvaluator looks back at a finished trajectory and scores each class
of runtime decision (model, tool, strategy, retrieval, verification,
recovery) with explicit evidence, an optional better alternative and a
confidence. Everything is deterministic: scores come from recorded traces
and failure records, never from a guess.
"""

from __future__ import annotations

import json

from atlas.adaptation.domain import DecisionQuality
from atlas.infra.db import Database
from atlas.memory.trajectory import (
    DecisionOutcome,
    DecisionPoint,
    DecisionTrace,
    FailureRecord,
    Trajectory,
)

_OUTCOME_SCORE: dict[DecisionOutcome, float] = {
    DecisionOutcome.SUCCESS: 1.0,
    DecisionOutcome.SUBOPTIMAL: 0.5,
    DecisionOutcome.FAILURE: 0.0,
    DecisionOutcome.UNKNOWN: 0.5,
}

#: Failure-record components that indicate retrieval was insufficient.
_RETRIEVAL_COMPONENTS = frozenset({"knowledge", "retrieval", "memory", "search"})


def _confidence(n_evidence: int) -> float:
    return min(1.0, 0.25 + 0.25 * n_evidence)


def _trace_scores(traces: tuple[DecisionTrace, ...]) -> tuple[float, tuple[str, ...], str]:
    """Average outcome score, evidence strings and a better-alternative hint
    for one decision point."""
    scored = tuple(t for t in traces if t.outcome in _OUTCOME_SCORE)
    if not scored:
        return 0.0, (), ""
    score = sum(_OUTCOME_SCORE[t.outcome] for t in scored) / len(scored)
    evidence = tuple(f"{t.decision_point.value}: chose {t.chosen_option} -> {t.outcome.value}" for t in scored)
    better = ""
    failed = [t for t in scored if t.outcome is DecisionOutcome.FAILURE]
    if failed:
        others = [o for o in failed[0].options_considered if o != failed[0].chosen_option]
        if others:
            better = others[0]
    return score, evidence, better


class ProcessEvaluator:
    """§31: was the selected model/tool/strategy appropriate? Was retrieval
    sufficient? Was verification appropriate? Was recovery sensible?"""

    def evaluate(
        self, trajectory: Trajectory, traces: tuple[DecisionTrace, ...], failures: tuple[FailureRecord, ...]
    ) -> tuple[DecisionQuality, ...]:
        results: list[DecisionQuality] = []

        def by_point(point: DecisionPoint) -> tuple[DecisionTrace, ...]:
            return tuple(t for t in traces if t.decision_point == point)

        # Model selection
        model_traces = by_point(DecisionPoint.MODEL_SELECTION)
        if model_traces:
            score, evidence, better = _trace_scores(model_traces)
            results.append(
                DecisionQuality(
                    trajectory_id=trajectory.id,
                    dimension="model_selection",
                    score=score,
                    evidence=evidence,
                    better_alternative=better,
                    confidence=_confidence(len(model_traces)),
                )
            )

        # Tool selection
        tool_traces = by_point(DecisionPoint.TOOL_SELECTION)
        if tool_traces:
            score, evidence, better = _trace_scores(tool_traces)
            results.append(
                DecisionQuality(
                    trajectory_id=trajectory.id,
                    dimension="tool_selection",
                    score=score,
                    evidence=evidence,
                    better_alternative=better,
                    confidence=_confidence(len(tool_traces)),
                )
            )

        # Strategy: success plus how much replanning was needed
        replans = len(by_point(DecisionPoint.REPLANNING))
        strategy_score = (1.0 if trajectory.success else 0.0) - 0.2 * min(replans, 2)
        results.append(
            DecisionQuality(
                trajectory_id=trajectory.id,
                dimension="strategy",
                score=max(0.0, strategy_score),
                evidence=(f"success={trajectory.success}, replans={replans}",),
                better_alternative="",
                confidence=_confidence(1 + replans),
            )
        )

        # Retrieval: insufficient when retrieval-side components failed
        retrieval_failures = tuple(f for f in failures if f.component in _RETRIEVAL_COMPONENTS)
        results.append(
            DecisionQuality(
                trajectory_id=trajectory.id,
                dimension="retrieval",
                score=0.0 if retrieval_failures else 1.0,
                evidence=tuple(f"{f.component}: {f.error_message}" for f in retrieval_failures)
                or ("no retrieval-side failures recorded",),
                better_alternative="",
                confidence=_confidence(len(retrieval_failures) or 1),
            )
        )

        # Verification
        verification_traces = by_point(DecisionPoint.VERIFICATION)
        if verification_traces:
            score, evidence, better = _trace_scores(verification_traces)
            results.append(
                DecisionQuality(
                    trajectory_id=trajectory.id,
                    dimension="verification",
                    score=score,
                    evidence=evidence,
                    better_alternative=better,
                    confidence=_confidence(len(verification_traces)),
                )
            )

        # Recovery: only meaningful when something actually failed
        if failures:
            recovered = trajectory.success
            results.append(
                DecisionQuality(
                    trajectory_id=trajectory.id,
                    dimension="recovery",
                    score=1.0 if recovered else 0.2,
                    evidence=(f"{len(failures)} failure(s), recovered={recovered}",),
                    better_alternative="",
                    confidence=_confidence(len(failures)),
                )
            )

        return tuple(results)


class DecisionQualityStore:
    """Persists decision-quality assessments (migration 017)."""

    def __init__(self, *, db: Database) -> None:
        self._db = db

    async def save_many(self, qualities: tuple[DecisionQuality, ...]) -> None:
        for quality in qualities:
            await self._db.conn.execute(
                """
                INSERT INTO decision_quality (
                    trajectory_id, dimension, score, evidence_json,
                    better_alternative, confidence, created_ts
                ) VALUES (?,?,?,?,?,?,?)
                """,
                (
                    quality.trajectory_id,
                    quality.dimension,
                    quality.score,
                    json.dumps(list(quality.evidence)),
                    quality.better_alternative,
                    quality.confidence,
                    quality.created_ts,
                ),
            )
        await self._db.conn.commit()

    async def for_trajectory(self, trajectory_id: str) -> tuple[DecisionQuality, ...]:
        cur = await self._db.conn.execute(
            "SELECT * FROM decision_quality WHERE trajectory_id=? ORDER BY dimension", (trajectory_id,)
        )
        rows = await cur.fetchall()
        result: list[DecisionQuality] = []
        for row in rows:
            d = dict(row)
            result.append(
                DecisionQuality(
                    trajectory_id=str(d["trajectory_id"]),
                    dimension=str(d["dimension"]),
                    score=float(d["score"]),
                    evidence=tuple(json.loads(str(d["evidence_json"]))),
                    better_alternative=str(d["better_alternative"]),
                    confidence=float(d["confidence"]),
                    created_ts=str(d["created_ts"]),
                )
            )
        return tuple(result)


__all__ = ["DecisionQualityStore", "ProcessEvaluator"]
