"""Experiment engine (Prompt 4 §19-§22, §86).

baseline → candidate → same benchmark → compare → generalization → safety →
decision. Experiments run OFFLINE (§20: sandbox / evaluation environment /
replay — never production first) against a FIXED baseline (§21). Every
experiment has hard resource limits; exceeding them aborts SAFELY and the
experiment is kept as evidence with status BUDGET_LIMITED (§86).
"""

from __future__ import annotations

import json
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field

from atlas.adaptation import statistics as stats
from atlas.adaptation.domain import (
    ComparisonResult,
    Experiment,
    ExperimentArm,
    ExperimentStatus,
    Hypothesis,
)
from atlas.infra.clock import Clock, SystemClock
from atlas.infra.db import Database
from atlas.infra.logging import get_logger

_log = get_logger("atlas.adaptation.experiments")


class ArmResults(BaseModel):
    """Per-task metric scores for one arm + what it cost to run."""

    model_config = ConfigDict(frozen=True)

    per_task: dict[str, dict[str, float]] = Field(default_factory=dict)
    cost_usd: float = 0.0
    tokens: int = 0


class ArmRunner(Protocol):
    """Executes one arm offline (sandbox/eval/replay). Production code never
    runs here; tests inject deterministic runners."""

    async def run_arm(self, experiment: Experiment, arm: ExperimentArm) -> ArmResults: ...


class ExperimentEngine:
    def __init__(self, *, store: ExperimentStore, clock: Clock | None = None) -> None:
        self._store = store
        self._clock = clock or SystemClock()

    async def create(
        self,
        hypothesis: Hypothesis,
        *,
        dataset_version: str,
        pipeline_version: str,
        atlas_version: str,
    ) -> Experiment:
        """§19: every experiment carries baseline, candidate, dataset,
        configuration, metrics and resource limits."""
        experiment = Experiment(
            hypothesis_id=hypothesis.hypothesis_id,
            baseline=ExperimentArm(arm="BASELINE"),
            candidate=ExperimentArm(arm="CANDIDATE", configuration={"change": hypothesis.proposed_change}),
            dataset_version=dataset_version,
            pipeline_version=pipeline_version,
            atlas_version=atlas_version,
        )
        await self._store.save(experiment)
        await self._store.record_event(
            "experiment_created", experiment.experiment_id, {"hypothesis": hypothesis.hypothesis_id}
        )
        return experiment

    async def run(self, experiment: Experiment, runner: ArmRunner) -> Experiment:
        """Runs baseline then candidate under identical limits; enforces the
        resource budget after each arm (§86: abort safely, never silently)."""
        limits = experiment.resource_limits
        total_cost = 0.0

        async def run_one(arm: ExperimentArm) -> ArmResults | None:
            nonlocal total_cost
            if total_cost >= limits.max_cost_usd:
                return None
            results = await runner.run_arm(experiment, arm)
            total_cost += results.cost_usd
            return results

        started = Experiment(**{**experiment.model_dump(), "status": ExperimentStatus.RUNNING})
        await self._store.save(started)

        baseline_results = await run_one(experiment.baseline)
        if baseline_results is None or total_cost > limits.max_cost_usd:
            return await self._abort(started, "baseline exceeded resource limits")

        candidate_results = await run_one(experiment.candidate)
        if candidate_results is None or total_cost > limits.max_cost_usd:
            return await self._abort(started, "candidate exceeded resource limits")

        # §21-22: per-metric paired comparison against the fixed baseline.
        for metric in experiment.metrics:
            baseline_scores, candidate_scores, paired = _paired_scores(
                baseline_results.per_task, candidate_results.per_task, metric
            )
            n = min(len(baseline_scores), len(candidate_scores))
            if n == 0:
                continue
            ci_low, ci_high = stats.confidence_interval(baseline_scores, candidate_scores, paired=paired)
            size = stats.effect_size(baseline_scores, candidate_scores)
            comparison = ComparisonResult(
                experiment_id=experiment.experiment_id,
                metric=metric,
                baseline_version=_arm_version(experiment.baseline) or experiment.atlas_version,
                candidate_version=_arm_version(experiment.candidate) or "candidate",
                dataset_version=experiment.dataset_version,
                model_version=experiment.baseline.model or "",
                atlas_version=experiment.atlas_version,
                n=n,
                baseline_mean=round(stats.mean(baseline_scores), 6),
                candidate_mean=round(stats.mean(candidate_scores), 6),
                baseline_median=round(stats.median(baseline_scores), 6),
                candidate_median=round(stats.median(candidate_scores), 6),
                baseline_variance=round(stats.variance(baseline_scores), 6),
                candidate_variance=round(stats.variance(candidate_scores), 6),
                confidence_interval_low=ci_low if ci_low is None else round(ci_low, 6),
                confidence_interval_high=ci_high if ci_high is None else round(ci_high, 6),
                effect_size=size if size is None else round(size, 4),
                paired=paired,
                significant=stats.is_significant(baseline_scores, candidate_scores, ci_low=ci_low, ci_high=ci_high),
            )
            await self._store.save_comparison(comparison)

        completed = Experiment(
            **{
                **started.model_dump(),
                "baseline": ExperimentArm(
                    **{**experiment.baseline.model_dump(), "n_tasks": len(baseline_results.per_task)}
                ),
                "candidate": ExperimentArm(
                    **{**experiment.candidate.model_dump(), "n_tasks": len(candidate_results.per_task)}
                ),
                "status": ExperimentStatus.COMPLETED,
                "completed_ts": self._clock.now().isoformat(),
            }
        )
        await self._store.save(completed)
        await self._store.record_event(
            "experiment_completed",
            experiment.experiment_id,
            {"cost_usd": total_cost, "note": stats.strength_note(len(baseline_results.per_task))},
        )
        _log.info(
            "experiment.completed",
            event_type="adaptation",
            experiment_id=experiment.experiment_id,
            cost_usd=total_cost,
        )
        return completed

    async def comparisons_for(self, experiment_id: str) -> tuple[ComparisonResult, ...]:
        """Public delegation so callers never reach into the store."""
        return await self._store.comparisons_for(experiment_id)

    async def _abort(self, experiment: Experiment, reason: str) -> Experiment:
        """§86: budget limit → ABORT SAFELY and keep the record as evidence."""
        aborted = Experiment(
            **{
                **experiment.model_dump(),
                "status": ExperimentStatus.BUDGET_LIMITED,
                "completed_ts": self._clock.now().isoformat(),
            }
        )
        await self._store.save(aborted)
        await self._store.record_event("experiment_budget_limited", experiment.experiment_id, {"reason": reason})
        _log.warning(
            "experiment.budget_limited",
            event_type="adaptation",
            experiment_id=experiment.experiment_id,
            reason=reason,
        )
        return aborted


def _arm_version(arm: ExperimentArm) -> str | None:
    return arm.strategy_version


def _paired_scores(
    baseline_tasks: dict[str, dict[str, float]],
    candidate_tasks: dict[str, dict[str, float]],
    metric: str,
) -> tuple[list[float], list[float], bool]:
    """Aligned per-task scores. Paired only when the SAME tasks ran under
    both arms (§22)."""
    shared = [
        t
        for t in baseline_tasks
        if t in candidate_tasks and metric in baseline_tasks[t] and metric in candidate_tasks[t]
    ]
    if len(shared) >= 2:
        return (
            [baseline_tasks[t][metric] for t in shared],
            [candidate_tasks[t][metric] for t in shared],
            True,
        )
    baseline_scores = [m[metric] for m in baseline_tasks.values() if metric in m]
    candidate_scores = [m[metric] for m in candidate_tasks.values() if metric in m]
    return baseline_scores, candidate_scores, False


class ExperimentStore:
    def __init__(self, *, db: Database, clock: Clock | None = None) -> None:
        self._db = db
        self._clock = clock or SystemClock()

    async def save(self, experiment: Experiment) -> None:
        await self._db.conn.execute(
            """
            INSERT OR REPLACE INTO experiments (
                experiment_id, hypothesis_id, baseline_json, candidate_json,
                dataset_version, pipeline_version, atlas_version, metrics_json,
                resource_limits_json, status, created_ts, completed_ts
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                experiment.experiment_id,
                experiment.hypothesis_id,
                experiment.baseline.model_dump_json(),
                experiment.candidate.model_dump_json(),
                experiment.dataset_version,
                experiment.pipeline_version,
                experiment.atlas_version,
                json.dumps(list(experiment.metrics)),
                experiment.resource_limits.model_dump_json(),
                experiment.status.value,
                experiment.created_ts,
                experiment.completed_ts,
            ),
        )
        await self._db.conn.commit()

    async def get(self, experiment_id: str) -> Experiment | None:
        cur = await self._db.conn.execute("SELECT * FROM experiments WHERE experiment_id=?", (experiment_id,))
        row = await cur.fetchone()
        return _from_row(row) if row is not None else None

    async def by_status(self, status: ExperimentStatus) -> tuple[Experiment, ...]:
        cur = await self._db.conn.execute(
            "SELECT * FROM experiments WHERE status=? ORDER BY created_ts", (status.value,)
        )
        rows = await cur.fetchall()
        return tuple(e for r in rows if (e := _from_row(r)) is not None)

    async def all(self) -> tuple[Experiment, ...]:
        cur = await self._db.conn.execute("SELECT * FROM experiments ORDER BY created_ts DESC")
        rows = await cur.fetchall()
        return tuple(e for r in rows if (e := _from_row(r)) is not None)

    async def save_comparison(self, comparison: ComparisonResult) -> None:
        await self._db.conn.execute(
            """
            INSERT INTO comparison_results (
                experiment_id, metric, baseline_version, candidate_version,
                dataset_version, model_version, atlas_version, n, baseline_mean,
                candidate_mean, baseline_median, candidate_median,
                baseline_variance, candidate_variance, ci_low, ci_high,
                effect_size, paired, significant, created_ts
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                comparison.experiment_id,
                comparison.metric,
                comparison.baseline_version,
                comparison.candidate_version,
                comparison.dataset_version,
                comparison.model_version,
                comparison.atlas_version,
                comparison.n,
                comparison.baseline_mean,
                comparison.candidate_mean,
                comparison.baseline_median,
                comparison.candidate_median,
                comparison.baseline_variance,
                comparison.candidate_variance,
                comparison.confidence_interval_low,
                comparison.confidence_interval_high,
                comparison.effect_size,
                int(comparison.paired),
                int(comparison.significant),
                self._clock.now().isoformat(),
            ),
        )
        await self._db.conn.commit()

    async def comparisons_for(self, experiment_id: str) -> tuple[ComparisonResult, ...]:
        cur = await self._db.conn.execute(
            "SELECT * FROM comparison_results WHERE experiment_id=? ORDER BY metric",
            (experiment_id,),
        )
        rows = await cur.fetchall()
        result: list[ComparisonResult] = []
        for row in rows:
            d = dict(row)
            result.append(
                ComparisonResult(
                    experiment_id=d["experiment_id"],
                    metric=d["metric"],
                    baseline_version=d["baseline_version"],
                    candidate_version=d["candidate_version"],
                    dataset_version=d["dataset_version"],
                    model_version=d["model_version"],
                    atlas_version=d["atlas_version"],
                    n=d["n"],
                    baseline_mean=d["baseline_mean"],
                    candidate_mean=d["candidate_mean"],
                    baseline_median=d["baseline_median"],
                    candidate_median=d["candidate_median"],
                    baseline_variance=d["baseline_variance"],
                    candidate_variance=d["candidate_variance"],
                    confidence_interval_low=d["ci_low"],
                    confidence_interval_high=d["ci_high"],
                    effect_size=d["effect_size"],
                    paired=bool(d["paired"]),
                    significant=bool(d["significant"]),
                )
            )
        return tuple(result)

    async def record_event(self, kind: str, ref_id: str, detail: dict[str, object]) -> None:
        await self._db.conn.execute(
            "INSERT INTO adaptation_events (ts, kind, ref_id, detail_json) VALUES (?,?,?,?)",
            (self._clock.now().isoformat(), kind, ref_id, json.dumps(detail)),
        )
        await self._db.conn.commit()


def _from_row(row: object) -> Experiment | None:
    if row is None:
        return None
    from atlas.adaptation.domain import ResourceLimits

    d = dict(row)  # type: ignore[call-overload]
    return Experiment(
        experiment_id=d["experiment_id"],
        hypothesis_id=d["hypothesis_id"],
        baseline=ExperimentArm.model_validate_json(d["baseline_json"]),
        candidate=ExperimentArm.model_validate_json(d["candidate_json"]),
        dataset_version=d["dataset_version"],
        pipeline_version=d["pipeline_version"],
        atlas_version=d["atlas_version"],
        metrics=tuple(json.loads(d["metrics_json"])),
        resource_limits=ResourceLimits.model_validate_json(d["resource_limits_json"]),
        status=ExperimentStatus(d["status"]),
        created_ts=d["created_ts"],
        completed_ts=d["completed_ts"],
    )


__all__ = ["ArmResults", "ArmRunner", "ExperimentEngine", "ExperimentStore"]
