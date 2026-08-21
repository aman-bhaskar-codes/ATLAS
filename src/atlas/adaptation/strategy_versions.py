"""Immutable strategy versioning + performance tracking (Prompt 4 §13-§14).

§13: "Never overwrite active strategy definitions in place." Every change
creates a new StrategyVersion row with change_reason + source_experiments.
§14: performance is multi-dimensional — success rate alone is never enough
("a strategy that succeeds 2% more often but costs 10x more may not be
superior").

The existing `strategies` table in memory/ stays the LIVE-selection surface;
this store is the versioned history + measured performance behind it.
"""

from __future__ import annotations

import json

from atlas.adaptation.domain import StrategyPerformance, StrategyVersion
from atlas.infra.clock import Clock, SystemClock
from atlas.infra.db import Database
from atlas.infra.logging import get_logger

_log = get_logger("atlas.adaptation.strategies")


class StrategyVersionStore:
    def __init__(self, *, db: Database, clock: Clock | None = None) -> None:
        self._db = db
        self._clock = clock or SystemClock()

    async def save_version(self, version: StrategyVersion) -> None:
        """Insert-only: saving over an existing (strategy_id, version) is a
        bug, not an update — versions are immutable."""
        cur = await self._db.conn.execute(
            "SELECT 1 FROM strategy_versions WHERE strategy_id=? AND version=?",
            (version.strategy_id, version.version),
        )
        if await cur.fetchone() is not None:
            raise ValueError(
                f"strategy {version.strategy_id} v{version.version} already exists — versions are immutable (§13)"
            )
        await self._db.conn.execute(
            """
            INSERT INTO strategy_versions (
                strategy_id, version, definition, task_type_pattern, skills_json,
                retrieval_policy, model_preference, tool_preference,
                verification_policy, change_reason, source_experiments_json, created_ts
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                version.strategy_id,
                version.version,
                version.definition,
                version.task_type_pattern,
                json.dumps(list(version.skills)),
                version.retrieval_policy,
                version.model_preference,
                version.tool_preference,
                version.verification_policy,
                version.change_reason,
                json.dumps(list(version.source_experiments)),
                version.created_ts,
            ),
        )
        await self._db.conn.commit()
        _log.info(
            "strategy_version.saved",
            event_type="adaptation",
            strategy_id=version.strategy_id,
            version=version.version,
        )

    async def next_version(self, strategy_id: str) -> int:
        cur = await self._db.conn.execute(
            "SELECT COALESCE(MAX(version), 0) AS v FROM strategy_versions WHERE strategy_id=?",
            (strategy_id,),
        )
        row = await cur.fetchone()
        assert row is not None
        return int(row["v"]) + 1

    async def get_version(self, strategy_id: str, version: int) -> StrategyVersion | None:
        cur = await self._db.conn.execute(
            "SELECT * FROM strategy_versions WHERE strategy_id=? AND version=?",
            (strategy_id, version),
        )
        row = await cur.fetchone()
        return _version_from_row(row) if row is not None else None

    async def latest(self, strategy_id: str) -> StrategyVersion | None:
        cur = await self._db.conn.execute(
            "SELECT * FROM strategy_versions WHERE strategy_id=? ORDER BY version DESC LIMIT 1",
            (strategy_id,),
        )
        row = await cur.fetchone()
        return _version_from_row(row) if row is not None else None

    async def versions(self, strategy_id: str) -> tuple[StrategyVersion, ...]:
        cur = await self._db.conn.execute(
            "SELECT * FROM strategy_versions WHERE strategy_id=? ORDER BY version",
            (strategy_id,),
        )
        rows = await cur.fetchall()
        return tuple(v for r in rows if (v := _version_from_row(r)) is not None)


def _version_from_row(row: object) -> StrategyVersion | None:
    if row is None:
        return None
    d = dict(row)  # type: ignore[call-overload]
    return StrategyVersion(
        strategy_id=d["strategy_id"],
        version=d["version"],
        definition=d["definition"],
        task_type_pattern=d["task_type_pattern"],
        skills=tuple(json.loads(d["skills_json"])),
        retrieval_policy=d["retrieval_policy"],
        model_preference=d["model_preference"],
        tool_preference=d["tool_preference"],
        verification_policy=d["verification_policy"],
        change_reason=d["change_reason"],
        source_experiments=tuple(json.loads(d["source_experiments_json"])),
        created_ts=d["created_ts"],
    )


class StrategyPerformanceTracker:
    """§14 running statistics per (strategy_id, version). Averages are kept
    incrementally: new_avg = old + (x - old) / n."""

    def __init__(self, *, db: Database, clock: Clock | None = None) -> None:
        self._db = db
        self._clock = clock or SystemClock()

    async def record_outcome(
        self,
        strategy_id: str,
        version: int,
        *,
        success: bool,
        quality_score: float = 0.0,
        latency_ms: float = 0.0,
        cost_usd: float = 0.0,
        recovered: bool = False,
        verified: bool = False,
        user_feedback: float = 0.0,
    ) -> StrategyPerformance:
        current = await self.get(strategy_id, version)
        n = current.runs + 1

        def avg(old: float, sample: float) -> float:
            return old + (sample - old) / n

        updated = StrategyPerformance(
            strategy_id=strategy_id,
            version=version,
            runs=n,
            success_rate=avg(current.success_rate, 1.0 if success else 0.0),
            quality_score=avg(current.quality_score, quality_score),
            latency_ms_avg=avg(current.latency_ms_avg, latency_ms),
            cost_usd_avg=avg(current.cost_usd_avg, cost_usd),
            recovery_rate=avg(current.recovery_rate, 1.0 if recovered else 0.0),
            verification_rate=avg(current.verification_rate, 1.0 if verified else 0.0),
            generalization=current.generalization,  # set by the generalization gate
            user_feedback=avg(current.user_feedback, user_feedback) if user_feedback else current.user_feedback,
            updated_ts=self._clock.now().isoformat(),
        )
        await self._db.conn.execute(
            """
            INSERT OR REPLACE INTO strategy_performance (
                strategy_id, version, runs, success_rate, quality_score,
                latency_ms_avg, cost_usd_avg, recovery_rate, verification_rate,
                generalization, user_feedback, updated_ts
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                updated.strategy_id,
                updated.version,
                updated.runs,
                updated.success_rate,
                updated.quality_score,
                updated.latency_ms_avg,
                updated.cost_usd_avg,
                updated.recovery_rate,
                updated.verification_rate,
                updated.generalization,
                updated.user_feedback,
                updated.updated_ts,
            ),
        )
        await self._db.conn.commit()
        return updated

    async def set_generalization(self, strategy_id: str, version: int, score: float) -> None:
        await self._db.conn.execute(
            "UPDATE strategy_performance SET generalization=?, updated_ts=? WHERE strategy_id=? AND version=?",
            (score, self._clock.now().isoformat(), strategy_id, version),
        )
        await self._db.conn.commit()

    async def get(self, strategy_id: str, version: int) -> StrategyPerformance:
        cur = await self._db.conn.execute(
            "SELECT * FROM strategy_performance WHERE strategy_id=? AND version=?",
            (strategy_id, version),
        )
        row = await cur.fetchone()
        if row is None:
            return StrategyPerformance(strategy_id=strategy_id, version=version)
        d = dict(row)
        return StrategyPerformance(
            strategy_id=d["strategy_id"],
            version=d["version"],
            runs=d["runs"],
            success_rate=d["success_rate"],
            quality_score=d["quality_score"],
            latency_ms_avg=d["latency_ms_avg"],
            cost_usd_avg=d["cost_usd_avg"],
            recovery_rate=d["recovery_rate"],
            verification_rate=d["verification_rate"],
            generalization=d["generalization"],
            user_feedback=d["user_feedback"],
            updated_ts=d["updated_ts"],
        )


__all__ = ["StrategyPerformanceTracker", "StrategyVersionStore"]
