"""Canary adaptation — graduated live rollout of validated low-risk
strategies (Prompt 4 §26, §72).

The candidate handles a small percentage of eligible tasks; success,
regression, latency, cost and safety are observed; the rollout expands
5 -> 10 -> 25 -> 50 -> 100% only while metrics hold, and rolls back
automatically on severe regression. Single-user mode uses small task-count
canaries: decisions are made once at least `min_tasks` observations exist.
"""

from __future__ import annotations

import zlib
from dataclasses import dataclass

from atlas.adaptation.domain import CanaryDeployment, CanaryObservation, CanaryStatus
from atlas.infra.clock import Clock, SystemClock
from atlas.infra.db import Database
from atlas.infra.logging import get_logger

_log = get_logger("atlas.adaptation.canary")

#: Graduated rollout steps (§26).
CANARY_STEPS: tuple[float, ...] = (5.0, 10.0, 25.0, 50.0, 100.0)

#: Minimum observations per step before expand/rollback decisions (§26
#: single-user task-count canaries).
DEFAULT_MIN_TASKS_PER_STEP = 5

#: Success-rate drop vs baseline that counts as severe regression.
_SEVERE_REGRESSION_DELTA = 0.10


@dataclass(frozen=True)
class CanaryMetrics:
    """Aggregate observations for one deployment."""

    n: int = 0
    success_rate: float = 0.0
    regression_count: int = 0
    safety_events: int = 0
    latency_ms_avg: float = 0.0
    cost_usd_avg: float = 0.0


class CanaryManager:
    """Deploys, routes, observes and expands/rolls back canary strategies."""

    def __init__(
        self, *, db: Database, clock: Clock | None = None, min_tasks: int = DEFAULT_MIN_TASKS_PER_STEP
    ) -> None:
        self._db = db
        self._clock = clock or SystemClock()
        self._min_tasks = min_tasks

    # ── deployment ──────────────────────────────────────────────────────

    async def deploy(self, strategy_id: str, version: int) -> CanaryDeployment:
        """Start a canary at the smallest step (§26)."""
        deployment = CanaryDeployment(strategy_id=strategy_id, version=version, percentage=CANARY_STEPS[0])
        await self._save_deployment(deployment)
        _log.info("canary.deployed", event_type="adaptation", strategy_id=strategy_id, version=version)
        return deployment

    def route_task(self, deployment: CanaryDeployment, task_id: str) -> bool:
        """Deterministic routing: a stable hash of the task id decides which
        arm handles it, so the same task never flips arms mid-rollout."""
        return zlib.crc32(task_id.encode()) % 100 < deployment.percentage

    # ── observation ─────────────────────────────────────────────────────

    async def observe(self, observation: CanaryObservation) -> None:
        await self._db.conn.execute(
            """
            INSERT INTO canary_observations (
                deployment_id, trajectory_id, success, regression, safety_event,
                latency_ms, cost_usd, ts
            ) VALUES (?,?,?,?,?,?,?,?)
            """,
            (
                observation.deployment_id,
                observation.trajectory_id,
                int(observation.success),
                int(observation.regression),
                int(observation.safety_event),
                observation.latency_ms,
                observation.cost_usd,
                observation.ts,
            ),
        )
        await self._db.conn.execute(
            "UPDATE canary_deployments SET tasks_seen=tasks_seen+1, updated_ts=? WHERE deployment_id=?",
            (self._clock.now().isoformat(), observation.deployment_id),
        )
        await self._db.conn.commit()

    async def metrics(self, deployment_id: str) -> CanaryMetrics:
        cur = await self._db.conn.execute(
            """
            SELECT COUNT(*) AS n,
                   AVG(success) AS success_rate,
                   SUM(regression) AS regressions,
                   SUM(safety_event) AS safety_events,
                   AVG(latency_ms) AS latency_avg,
                   AVG(cost_usd) AS cost_avg
            FROM canary_observations WHERE deployment_id=?
            """,
            (deployment_id,),
        )
        row = await cur.fetchone()
        if row is None or not row["n"]:
            return CanaryMetrics()
        return CanaryMetrics(
            n=int(row["n"]),
            success_rate=float(row["success_rate"] or 0.0),
            regression_count=int(row["regressions"] or 0),
            safety_events=int(row["safety_events"] or 0),
            latency_ms_avg=float(row["latency_avg"] or 0.0),
            cost_usd_avg=float(row["cost_avg"] or 0.0),
        )

    # ── evaluation: expand / hold / rollback ────────────────────────────

    async def evaluate(self, deployment: CanaryDeployment, *, baseline_success_rate: float) -> CanaryDeployment:
        """One canary review (§26): safety event or severe regression rolls
        back automatically; enough good observations expand to the next step;
        otherwise hold at the current percentage."""
        metrics = await self.metrics(deployment.deployment_id)
        now = self._clock.now().isoformat()
        if metrics.safety_events > 0:
            updated = _replace(deployment, status=CanaryStatus.ROLLED_BACK, updated_ts=now)
            await self._save_deployment(updated)
            _log.warning("canary.rollback_safety", event_type="adaptation", deployment_id=deployment.deployment_id)
            return updated
        if metrics.n < self._min_tasks:
            return deployment  # not enough evidence — hold, never guess
        severe = metrics.success_rate < baseline_success_rate - _SEVERE_REGRESSION_DELTA
        if severe or metrics.regression_count > 0:
            updated = _replace(deployment, status=CanaryStatus.ROLLED_BACK, updated_ts=now)
            await self._save_deployment(updated)
            _log.warning(
                "canary.rollback_regression",
                event_type="adaptation",
                deployment_id=deployment.deployment_id,
                success_rate=round(metrics.success_rate, 3),
            )
            return updated
        if metrics.success_rate < baseline_success_rate:
            return deployment  # slightly worse but not severe — keep observing
        # expanding
        try:
            next_step = CANARY_STEPS[CANARY_STEPS.index(deployment.percentage) + 1]
        except (ValueError, IndexError):
            next_step = None
        if next_step is None:
            updated = _replace(deployment, status=CanaryStatus.FULL, percentage=100.0, updated_ts=now)
        else:
            updated = _replace(
                deployment,
                percentage=next_step,
                status=CanaryStatus.FULL if next_step == 100.0 else CanaryStatus.EXPANDING,
                updated_ts=now,
            )
        await self._save_deployment(updated)
        _log.info(
            "canary.expanded",
            event_type="adaptation",
            deployment_id=deployment.deployment_id,
            percentage=updated.percentage,
        )
        return updated

    # ── reads ───────────────────────────────────────────────────────────

    async def get(self, deployment_id: str) -> CanaryDeployment | None:
        cur = await self._db.conn.execute("SELECT * FROM canary_deployments WHERE deployment_id=?", (deployment_id,))
        row = await cur.fetchone()
        if row is None:
            return None
        d = dict(row)
        return CanaryDeployment(
            deployment_id=d["deployment_id"],
            strategy_id=d["strategy_id"],
            version=d["version"],
            percentage=d["percentage"],
            status=CanaryStatus(d["status"]),
            tasks_seen=d["tasks_seen"],
            created_ts=d["created_ts"],
            updated_ts=d["updated_ts"],
        )

    async def active_for(self, strategy_id: str) -> CanaryDeployment | None:
        cur = await self._db.conn.execute(
            "SELECT * FROM canary_deployments WHERE strategy_id=? AND status != 'ROLLED_BACK'"
            " ORDER BY created_ts DESC LIMIT 1",
            (strategy_id,),
        )
        row = await cur.fetchone()
        if row is None:
            return None
        d = dict(row)
        return CanaryDeployment(
            deployment_id=d["deployment_id"],
            strategy_id=d["strategy_id"],
            version=d["version"],
            percentage=d["percentage"],
            status=CanaryStatus(d["status"]),
            tasks_seen=d["tasks_seen"],
            created_ts=d["created_ts"],
            updated_ts=d["updated_ts"],
        )

    # ── internals ───────────────────────────────────────────────────────

    async def _save_deployment(self, deployment: CanaryDeployment) -> None:
        await self._db.conn.execute(
            """
            INSERT OR REPLACE INTO canary_deployments (
                deployment_id, strategy_id, version, percentage, status,
                tasks_seen, created_ts, updated_ts
            ) VALUES (?,?,?,?,?,?,?,?)
            """,
            (
                deployment.deployment_id,
                deployment.strategy_id,
                deployment.version,
                deployment.percentage,
                deployment.status.value,
                deployment.tasks_seen,
                deployment.created_ts,
                deployment.updated_ts,
            ),
        )
        await self._db.conn.commit()


def _replace(deployment: CanaryDeployment, **changes: object) -> CanaryDeployment:
    return CanaryDeployment(**{**deployment.model_dump(), **changes})


__all__ = [
    "CANARY_STEPS",
    "DEFAULT_MIN_TASKS_PER_STEP",
    "CanaryManager",
    "CanaryMetrics",
]
