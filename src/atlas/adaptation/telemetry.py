"""Cognitive telemetry — per-trajectory cognitive dimension tracking
(Prompt 4 §32).

Collects planning quality, tool/model selection quality, retrieval and
memory usefulness, verification and recovery quality, research efficiency,
strategy transfer and confidence calibration — always linked to a
trajectory. Every value is derived from already-measured artifacts
(TrajectoryEvaluation, DecisionQuality); nothing here invents a number.
"""

from __future__ import annotations

from atlas.adaptation.domain import CognitiveTelemetry, DecisionQuality, TrajectoryEvaluation
from atlas.infra.db import Database


class CognitiveTelemetryCollector:
    """Derives §32 dimensions from measured evaluation artifacts."""

    def collect(
        self,
        trajectory_id: str,
        *,
        evaluation: TrajectoryEvaluation | None = None,
        qualities: tuple[DecisionQuality, ...] = (),
        strategy_transfer: float | None = None,
        confidence_calibration: float | None = None,
    ) -> CognitiveTelemetry:
        by_dim = {q.dimension: q for q in qualities}

        def quality_score(dimension: str) -> float | None:
            return by_dim[dimension].score if dimension in by_dim else None

        return CognitiveTelemetry(
            trajectory_id=trajectory_id,
            planning_quality=evaluation.planning_quality if evaluation else None,
            tool_selection_accuracy=_first_present(
                evaluation.tool_selection if evaluation else None, quality_score("tool_selection")
            ),
            model_selection_quality=quality_score("model_selection"),
            retrieval_usefulness=quality_score("retrieval"),
            memory_usefulness=evaluation.memory_usefulness if evaluation else None,
            verification_quality=_first_present(
                evaluation.verification if evaluation else None, quality_score("verification")
            ),
            recovery_quality=_first_present(
                evaluation.recovery_quality if evaluation else None, quality_score("recovery")
            ),
            research_efficiency=evaluation.efficiency if evaluation else None,
            strategy_transfer=strategy_transfer,
            confidence_calibration=confidence_calibration,
        )


def _first_present(*values: float | None) -> float | None:
    for value in values:
        if value is not None:
            return value
    return None


class TelemetryStore:
    """Persists cognitive telemetry rows (migration 018)."""

    def __init__(self, *, db: Database) -> None:
        self._db = db

    async def save(self, telemetry: CognitiveTelemetry) -> None:
        await self._db.conn.execute(
            """
            INSERT INTO cognitive_telemetry (
                trajectory_id, planning_quality, tool_selection_accuracy,
                model_selection_quality, retrieval_usefulness, memory_usefulness,
                verification_quality, recovery_quality, research_efficiency,
                strategy_transfer, confidence_calibration, created_ts
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                telemetry.trajectory_id,
                telemetry.planning_quality,
                telemetry.tool_selection_accuracy,
                telemetry.model_selection_quality,
                telemetry.retrieval_usefulness,
                telemetry.memory_usefulness,
                telemetry.verification_quality,
                telemetry.recovery_quality,
                telemetry.research_efficiency,
                telemetry.strategy_transfer,
                telemetry.confidence_calibration,
                telemetry.created_ts,
            ),
        )
        await self._db.conn.commit()

    async def for_trajectory(self, trajectory_id: str) -> tuple[CognitiveTelemetry, ...]:
        cur = await self._db.conn.execute(
            "SELECT * FROM cognitive_telemetry WHERE trajectory_id=? ORDER BY id", (trajectory_id,)
        )
        rows = await cur.fetchall()
        result: list[CognitiveTelemetry] = []
        for row in rows:
            d = dict(row)
            result.append(
                CognitiveTelemetry(
                    trajectory_id=str(d["trajectory_id"]),
                    planning_quality=d["planning_quality"],
                    tool_selection_accuracy=d["tool_selection_accuracy"],
                    model_selection_quality=d["model_selection_quality"],
                    retrieval_usefulness=d["retrieval_usefulness"],
                    memory_usefulness=d["memory_usefulness"],
                    verification_quality=d["verification_quality"],
                    recovery_quality=d["recovery_quality"],
                    research_efficiency=d["research_efficiency"],
                    strategy_transfer=d["strategy_transfer"],
                    confidence_calibration=d["confidence_calibration"],
                    created_ts=str(d["created_ts"]),
                )
            )
        return tuple(result)

    async def averages(self) -> dict[str, float]:
        """Mean of each dimension across all trajectories (None-safe)."""
        cur = await self._db.conn.execute(
            """
            SELECT AVG(planning_quality) AS planning_quality,
                   AVG(tool_selection_accuracy) AS tool_selection_accuracy,
                   AVG(model_selection_quality) AS model_selection_quality,
                   AVG(retrieval_usefulness) AS retrieval_usefulness,
                   AVG(memory_usefulness) AS memory_usefulness,
                   AVG(verification_quality) AS verification_quality,
                   AVG(recovery_quality) AS recovery_quality,
                   AVG(research_efficiency) AS research_efficiency,
                   AVG(strategy_transfer) AS strategy_transfer,
                   AVG(confidence_calibration) AS confidence_calibration
            FROM cognitive_telemetry
            """
        )
        row = await cur.fetchone()
        if row is None:
            return {}
        return {key: float(row[key]) for key in row.keys() if row[key] is not None}


__all__ = ["CognitiveTelemetryCollector", "TelemetryStore"]
