"""Adaptive routing with bounded exploration (Prompt 4 §35-§37).

Accumulates per-arm, per-task-class evidence (runs, success, quality,
latency, cost). Once enough evidence exists the router exploits the best
known arm ~90% of the time and explores alternatives ~10% (§36) — for both
models and strategies (§37). Before enough evidence exists it returns None
so the caller falls back to static capability-based routing (§35).

Exploration is bounded (fixed rate), safe (only caller-provided registered
options), measurable (exploration_runs counter) and reversible (stats can
be reset) (§37).
"""

from __future__ import annotations

import random

from atlas.adaptation.domain import ArmKind, RoutingChoice, RoutingStats
from atlas.infra.clock import Clock, SystemClock
from atlas.infra.db import Database
from atlas.infra.logging import get_logger

_log = get_logger("atlas.adaptation.routing")

#: §35: minimum runs per arm before learned routing replaces static routing.
DEFAULT_MIN_EVIDENCE = 5

#: §36: controlled exploration rate (90% exploit / 10% explore).
DEFAULT_EXPLORATION_RATE = 0.10


class RoutingStatsStore:
    """Persists routing evidence (migration 018 routing_stats)."""

    def __init__(self, *, db: Database, clock: Clock | None = None) -> None:
        self._db = db
        self._clock = clock or SystemClock()

    async def record_outcome(
        self,
        arm_kind: ArmKind,
        arm: str,
        task_class: str,
        *,
        success: bool,
        quality: float = 0.0,
        latency_ms: float = 0.0,
        cost_usd: float = 0.0,
        explored: bool = False,
    ) -> None:
        existing = await self.get(arm_kind, arm, task_class)
        now = self._clock.now().isoformat()
        if existing is None:
            stats = RoutingStats(
                arm_kind=arm_kind,
                arm=arm,
                task_class=task_class,
                runs=1,
                successes=int(success),
                quality_sum=quality,
                latency_sum=latency_ms,
                cost_sum=cost_usd,
                exploration_runs=int(explored),
                updated_ts=now,
            )
        else:
            stats = RoutingStats(
                arm_kind=arm_kind,
                arm=arm,
                task_class=task_class,
                runs=existing.runs + 1,
                successes=existing.successes + int(success),
                quality_sum=existing.quality_sum + quality,
                latency_sum=existing.latency_sum + latency_ms,
                cost_sum=existing.cost_sum + cost_usd,
                exploration_runs=existing.exploration_runs + int(explored),
                updated_ts=now,
            )
        await self._db.conn.execute(
            """
            INSERT OR REPLACE INTO routing_stats (
                arm_kind, arm, task_class, runs, successes, quality_sum,
                latency_sum, cost_sum, exploration_runs, updated_ts
            ) VALUES (?,?,?,?,?,?,?,?,?,?)
            """,
            (
                stats.arm_kind.value,
                stats.arm,
                stats.task_class,
                stats.runs,
                stats.successes,
                stats.quality_sum,
                stats.latency_sum,
                stats.cost_sum,
                stats.exploration_runs,
                stats.updated_ts,
            ),
        )
        await self._db.conn.commit()

    async def get(self, arm_kind: ArmKind, arm: str, task_class: str) -> RoutingStats | None:
        cur = await self._db.conn.execute(
            "SELECT * FROM routing_stats WHERE arm_kind=? AND arm=? AND task_class=?",
            (arm_kind.value, arm, task_class),
        )
        row = await cur.fetchone()
        if row is None:
            return None
        d = dict(row)
        return RoutingStats(
            arm_kind=ArmKind(str(d["arm_kind"])),
            arm=str(d["arm"]),
            task_class=str(d["task_class"]),
            runs=int(d["runs"]),
            successes=int(d["successes"]),
            quality_sum=float(d["quality_sum"]),
            latency_sum=float(d["latency_sum"]),
            cost_sum=float(d["cost_sum"]),
            exploration_runs=int(d["exploration_runs"]),
            updated_ts=str(d["updated_ts"]),
        )

    async def for_task_class(self, arm_kind: ArmKind, task_class: str) -> tuple[RoutingStats, ...]:
        cur = await self._db.conn.execute(
            "SELECT * FROM routing_stats WHERE arm_kind=? AND task_class=?", (arm_kind.value, task_class)
        )
        rows = await cur.fetchall()
        result: list[RoutingStats] = []
        for row in rows:
            d = dict(row)
            result.append(
                RoutingStats(
                    arm_kind=ArmKind(str(d["arm_kind"])),
                    arm=str(d["arm"]),
                    task_class=str(d["task_class"]),
                    runs=int(d["runs"]),
                    successes=int(d["successes"]),
                    quality_sum=float(d["quality_sum"]),
                    latency_sum=float(d["latency_sum"]),
                    cost_sum=float(d["cost_sum"]),
                    exploration_runs=int(d["exploration_runs"]),
                    updated_ts=str(d["updated_ts"]),
                )
            )
        return tuple(result)

    async def reset(self, arm_kind: ArmKind, task_class: str) -> None:
        """§37 reversibility: wipe learned evidence for one task class."""
        await self._db.conn.execute(
            "DELETE FROM routing_stats WHERE arm_kind=? AND task_class=?", (arm_kind.value, task_class)
        )
        await self._db.conn.commit()


class AdaptiveRouter:
    """§35-§37: evidence-gated exploit/explore routing for models and
    strategies. Returns None until evidence exists — the caller then uses
    static capability-based routing."""

    def __init__(
        self,
        *,
        store: RoutingStatsStore,
        min_evidence: int = DEFAULT_MIN_EVIDENCE,
        exploration_rate: float = DEFAULT_EXPLORATION_RATE,
        rng: random.Random | None = None,
    ) -> None:
        self._store = store
        self._min_evidence = min_evidence
        self._exploration_rate = exploration_rate
        self._rng = rng or random.Random()

    async def choose(self, arm_kind: ArmKind, task_class: str, options: tuple[str, ...]) -> RoutingChoice | None:
        if not options:
            return None
        stats = {arm: await self._store.get(arm_kind, arm, task_class) for arm in options}
        informed = {arm: s for arm, s in stats.items() if s is not None and s.runs >= self._min_evidence}
        if not informed:
            return None  # §35: not enough evidence — static routing stays in charge

        best_arm = max(informed, key=lambda arm: (informed[arm].success_rate, informed[arm].quality_avg))
        if len(options) > 1 and self._rng.random() < self._exploration_rate:
            candidates = [arm for arm in options if arm != best_arm]
            chosen = self._rng.choice(candidates)
            _log.info(
                "routing.explore", event_type="adaptation", arm_kind=arm_kind.value, task_class=task_class, arm=chosen
            )
            return RoutingChoice(arm=chosen, explored=True, reason="bounded exploration (§36)")
        return RoutingChoice(arm=best_arm, explored=False, reason="best measured arm (§35)")


__all__ = [
    "DEFAULT_EXPLORATION_RATE",
    "DEFAULT_MIN_EVIDENCE",
    "AdaptiveRouter",
    "RoutingStatsStore",
]
