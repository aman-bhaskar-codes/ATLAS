"""Counterfactual engine — "what would have happened if ATLAS had chosen
differently?" (Prompt 4 §27, §28, §30).

For failed/suboptimal decisions the engine generates alternative choices,
replays them ONLY through side-effect-free environments (§28), and — when an
alternative measurably wins across enough comparable trajectories — stores a
DecisionPreference as evidence (§30). Preferences are advisory inputs to
routing; they are NEVER turned into hard rules automatically.
"""

from __future__ import annotations

from atlas.adaptation.domain import (
    AdaptationPoint,
    CounterfactualMode,
    CounterfactualResult,
    DecisionPreference,
)
from atlas.adaptation.replay import ReplayEnvironment, mode_for
from atlas.infra.clock import Clock, SystemClock
from atlas.infra.db import Database
from atlas.infra.logging import get_logger
from atlas.memory.trajectory import DecisionOutcome, DecisionPoint, DecisionTrace, Trajectory

_log = get_logger("atlas.adaptation.counterfactual")

#: Runtime decision sites that map onto adaptation points. Safety sites
#: (SAFETY_TIER) are deliberately absent — safety is never a counterfactual
#: target. CRITIQUE/REPLANNING have no tuning surface today.
_ADAPTATION_POINT_BY_DECISION: dict[DecisionPoint, AdaptationPoint] = {
    DecisionPoint.MODEL_SELECTION: AdaptationPoint.MODEL_SELECTION,
    DecisionPoint.TOOL_SELECTION: AdaptationPoint.TOOL_SELECTION,
    DecisionPoint.ROUTING: AdaptationPoint.STRATEGY_SELECTION,
    DecisionPoint.PLANNING: AdaptationPoint.STRATEGY_SELECTION,
    DecisionPoint.VERIFICATION: AdaptationPoint.VERIFICATION_ORDERING,
}

_OUTCOME_SCORE: dict[DecisionOutcome, float] = {
    DecisionOutcome.SUCCESS: 1.0,
    DecisionOutcome.SUBOPTIMAL: 0.5,
    DecisionOutcome.FAILURE: 0.0,
}

#: §30: a preference needs this many comparable trajectories before it exists.
DEFAULT_PREFERENCE_EVIDENCE = 3
DEFAULT_PREFERENCE_WIN_RATE = 0.7


class CounterfactualEngine:
    """Generates counterfactuals and learns decision preferences from them."""

    def __init__(self, *, db: Database, clock: Clock | None = None) -> None:
        self._db = db
        self._clock = clock or SystemClock()

    async def generate(
        self,
        trajectory: Trajectory,
        traces: tuple[DecisionTrace, ...],
        *,
        action_class: str,
        replay: ReplayEnvironment | None = None,
        environment_state: dict[str, object] | None = None,
    ) -> tuple[CounterfactualResult, ...]:
        """§27: for each failed/suboptimal tunable decision, propose the
        alternatives that were considered. §28: refuses outright unless the
        action class is replayable."""
        mode = mode_for(action_class)
        if mode is None:
            _log.warning(
                "counterfactual.refused",
                event_type="adaptation",
                trajectory_id=trajectory.id,
                action_class=action_class,
                reason="not replayable (§28)",
            )
            return ()

        snapshot_id: str | None = None
        if replay is not None and environment_state is not None:
            snapshot_id = replay.snapshot(dict(environment_state))

        results: list[CounterfactualResult] = []
        for trace in traces:
            point = _ADAPTATION_POINT_BY_DECISION.get(trace.decision_point)
            if point is None or trace.outcome not in (DecisionOutcome.FAILURE, DecisionOutcome.SUBOPTIMAL):
                continue
            original_score = _OUTCOME_SCORE[trace.outcome]
            for alternative in trace.options_considered:
                if alternative == trace.chosen_option:
                    continue
                alt_outcome, alt_score = self._replay_alternative(replay, snapshot_id, alternative, mode)
                results.append(
                    CounterfactualResult(
                        trajectory_id=trajectory.id,
                        adaptation_point=point,
                        original_option=trace.chosen_option,
                        alternative_option=alternative,
                        original_outcome=trace.outcome.value,
                        alternative_outcome=alt_outcome,
                        mode=mode,
                        delta=alt_score - original_score if alt_score is not None else 0.0,
                        created_ts=self._clock.now().isoformat(),
                    )
                )
        for result in results:
            await self.save(result)
        if results:
            _log.info(
                "counterfactual.generated",
                event_type="adaptation",
                trajectory_id=trajectory.id,
                count=len(results),
                mode=mode.value,
            )
        return tuple(results)

    @staticmethod
    def _replay_alternative(
        replay: ReplayEnvironment | None,
        snapshot_id: str | None,
        alternative: str,
        mode: CounterfactualMode,
    ) -> tuple[str, float | None]:
        if replay is None or snapshot_id is None:
            return "", None  # no simulator — outcome stays unknown, never faked
        outcome = replay.replay(snapshot_id, alternative)
        return ("success" if outcome.success else "failure"), outcome.score

    # ── persistence ─────────────────────────────────────────────────────

    async def save(self, result: CounterfactualResult) -> None:
        await self._db.conn.execute(
            """
            INSERT OR REPLACE INTO counterfactuals (
                counterfactual_id, trajectory_id, adaptation_point,
                original_option, alternative_option, original_outcome,
                alternative_outcome, mode, delta, created_ts
            ) VALUES (?,?,?,?,?,?,?,?,?,?)
            """,
            (
                result.counterfactual_id,
                result.trajectory_id,
                result.adaptation_point.value,
                result.original_option,
                result.alternative_option,
                result.original_outcome,
                result.alternative_outcome,
                result.mode.value,
                result.delta,
                result.created_ts,
            ),
        )
        await self._db.conn.commit()

    async def for_trajectory(self, trajectory_id: str) -> tuple[CounterfactualResult, ...]:
        cur = await self._db.conn.execute(
            "SELECT * FROM counterfactuals WHERE trajectory_id=? ORDER BY created_ts", (trajectory_id,)
        )
        rows = await cur.fetchall()
        result: list[CounterfactualResult] = []
        for row in rows:
            d = dict(row)
            result.append(
                CounterfactualResult(
                    counterfactual_id=d["counterfactual_id"],
                    trajectory_id=d["trajectory_id"],
                    adaptation_point=AdaptationPoint(d["adaptation_point"]),
                    original_option=d["original_option"],
                    alternative_option=d["alternative_option"],
                    original_outcome=d["original_outcome"],
                    alternative_outcome=d["alternative_outcome"],
                    mode=CounterfactualMode(d["mode"]),
                    delta=d["delta"],
                    created_ts=d["created_ts"],
                )
            )
        return tuple(result)

    # ── §30 preference learning ─────────────────────────────────────────

    async def learn_preferences(
        self,
        *,
        min_evidence: int = DEFAULT_PREFERENCE_EVIDENCE,
        min_win_rate: float = DEFAULT_PREFERENCE_WIN_RATE,
        source_experiment: str | None = None,
    ) -> tuple[DecisionPreference, ...]:
        """Aggregate measured counterfactuals; when one alternative beats the
        original across >= min_evidence comparable trajectories with a win
        rate >= min_win_rate, store a DecisionPreference. Evidence only —
        never a hard rule (§30)."""
        cur = await self._db.conn.execute(
            """
            SELECT adaptation_point, original_option, alternative_option,
                   COUNT(*) AS n,
                   SUM(CASE WHEN delta > 0 THEN 1 ELSE 0 END) AS wins
            FROM counterfactuals
            WHERE alternative_outcome != ''
            GROUP BY adaptation_point, original_option, alternative_option
            """
        )
        rows = await cur.fetchall()
        store = PreferenceStore(db=self._db)
        created: list[DecisionPreference] = []
        for row in rows:
            n = int(row["n"])
            wins = int(row["wins"] or 0)
            if n < min_evidence:
                continue
            win_rate = wins / n
            if win_rate < min_win_rate:
                continue
            point = AdaptationPoint(str(row["adaptation_point"]))
            alternative = str(row["alternative_option"])
            if await store.active_for(point, preferred_option=alternative) is not None:
                continue  # preference already learned for this option
            preference = DecisionPreference(
                adaptation_point=point,
                context_key="",
                preferred_option=alternative,
                evidence_count=n,
                success_rate=win_rate,
                source_experiment=source_experiment,
                created_ts=self._clock.now().isoformat(),
            )
            await store.save(preference)
            created.append(preference)
            _log.info(
                "preference.learned",
                event_type="adaptation",
                point=point.value,
                preferred=alternative,
                evidence=n,
                win_rate=round(win_rate, 3),
            )
        return tuple(created)


class PreferenceStore:
    """Persists decision preferences (migration 015 decision_preferences)."""

    def __init__(self, *, db: Database) -> None:
        self._db = db

    async def save(self, preference: DecisionPreference) -> None:
        await self._db.conn.execute(
            """
            INSERT OR REPLACE INTO decision_preferences (
                preference_id, adaptation_point, context_key, preferred_option,
                evidence_count, success_rate, source_experiment, active, created_ts
            ) VALUES (?,?,?,?,?,?,?,?,?)
            """,
            (
                preference.preference_id,
                preference.adaptation_point.value,
                preference.context_key,
                preference.preferred_option,
                preference.evidence_count,
                preference.success_rate,
                preference.source_experiment,
                int(preference.active),
                preference.created_ts,
            ),
        )
        await self._db.conn.commit()

    async def active_for(
        self, adaptation_point: AdaptationPoint, *, context_key: str = "", preferred_option: str = ""
    ) -> DecisionPreference | None:
        query = "SELECT * FROM decision_preferences WHERE adaptation_point=? AND active=1"
        params: list[object] = [adaptation_point.value]
        if preferred_option:
            query += " AND preferred_option=?"
            params.append(preferred_option)
        else:
            query += " AND context_key=?"
            params.append(context_key)
        query += " ORDER BY created_ts DESC LIMIT 1"
        cur = await self._db.conn.execute(query, tuple(params))
        row = await cur.fetchone()
        if row is None:
            return None
        return _pref_from_row(dict(row))

    async def deactivate(self, preference_id: str) -> None:
        await self._db.conn.execute("UPDATE decision_preferences SET active=0 WHERE preference_id=?", (preference_id,))
        await self._db.conn.commit()


def _pref_from_row(d: dict[str, object]) -> DecisionPreference:
    return DecisionPreference(
        preference_id=str(d["preference_id"]),
        adaptation_point=AdaptationPoint(str(d["adaptation_point"])),
        context_key=str(d["context_key"]),
        preferred_option=str(d["preferred_option"]),
        evidence_count=int(d["evidence_count"]),  # type: ignore[call-overload]
        success_rate=float(d["success_rate"]),  # type: ignore[arg-type]
        source_experiment=d["source_experiment"],  # type: ignore[arg-type]
        active=bool(d["active"]),
        created_ts=str(d["created_ts"]),
    )


__all__ = [
    "DEFAULT_PREFERENCE_EVIDENCE",
    "DEFAULT_PREFERENCE_WIN_RATE",
    "CounterfactualEngine",
    "PreferenceStore",
]
