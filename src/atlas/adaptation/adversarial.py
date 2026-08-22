"""Adversarial evaluation — robustness under perturbations (Prompt 4 §40).

Strategies are tested against ambiguity, wrong assumptions, missing data,
contradictory evidence, prompt injection, malicious documents, tool/provider
failure, stale information and unexpected UI state. A robust strategy must
survive reasonable perturbations; survival rates are stored per strategy per
perturbation class. No results are fabricated: without a runner there are no
results.
"""

from __future__ import annotations

from typing import Protocol

from atlas.adaptation.domain import AdversarialResult, PerturbationKind
from atlas.infra.clock import Clock, SystemClock
from atlas.infra.db import Database
from atlas.infra.logging import get_logger

_log = get_logger("atlas.adaptation.adversarial")

#: Survival rate below which a strategy is NOT considered robust for the
#: perturbation class (§40).
DEFAULT_ROBUSTNESS_THRESHOLD = 0.8


class AdversarialRunner(Protocol):
    """Executes perturbed tasks for one strategy and reports per-task
    survival. Implementations decide how each perturbation is injected."""

    async def run(
        self, strategy_id: str, perturbation: PerturbationKind, tasks: tuple[str, ...]
    ) -> tuple[bool, ...]: ...


class AdversarialEvaluator:
    """Runs the §40 perturbation catalogue and persists survival evidence."""

    def __init__(self, *, db: Database, runner: AdversarialRunner, clock: Clock | None = None) -> None:
        self._db = db
        self._runner = runner
        self._clock = clock or SystemClock()

    async def evaluate(
        self, strategy_id: str, perturbation: PerturbationKind, tasks: tuple[str, ...]
    ) -> AdversarialResult:
        if not tasks:
            msg = "adversarial evaluation needs at least one task (§48: no fake results)"
            raise ValueError(msg)
        outcomes = await self._runner.run(strategy_id, perturbation, tasks)
        survived = sum(1 for ok in outcomes if ok)
        result = AdversarialResult(
            strategy_id=strategy_id,
            perturbation=perturbation,
            n_tasks=len(tasks),
            survived=survived,
            survival_rate=survived / len(tasks),
            created_ts=self._clock.now().isoformat(),
        )
        await self._db.conn.execute(
            """
            INSERT INTO adversarial_results (
                strategy_id, perturbation, n_tasks, survived, survival_rate, created_ts
            ) VALUES (?,?,?,?,?,?)
            """,
            (
                result.strategy_id,
                result.perturbation.value,
                result.n_tasks,
                result.survived,
                result.survival_rate,
                result.created_ts,
            ),
        )
        await self._db.conn.commit()
        _log.info(
            "adversarial.evaluated",
            event_type="adaptation",
            strategy_id=strategy_id,
            perturbation=perturbation.value,
            survival_rate=round(result.survival_rate, 3),
        )
        return result

    async def evaluate_all(self, strategy_id: str, tasks: tuple[str, ...]) -> tuple[AdversarialResult, ...]:
        """The full §40 catalogue against one strategy."""
        results: list[AdversarialResult] = []
        for perturbation in PerturbationKind:
            results.append(await self.evaluate(strategy_id, perturbation, tasks))
        return tuple(results)

    def is_robust(self, result: AdversarialResult, *, threshold: float = DEFAULT_ROBUSTNESS_THRESHOLD) -> bool:
        return result.survival_rate >= threshold

    async def for_strategy(self, strategy_id: str) -> tuple[AdversarialResult, ...]:
        cur = await self._db.conn.execute(
            "SELECT * FROM adversarial_results WHERE strategy_id=? ORDER BY perturbation, id",
            (strategy_id,),
        )
        rows = await cur.fetchall()
        results: list[AdversarialResult] = []
        for row in rows:
            d = dict(row)
            results.append(
                AdversarialResult(
                    strategy_id=str(d["strategy_id"]),
                    perturbation=PerturbationKind(str(d["perturbation"])),
                    n_tasks=int(d["n_tasks"]),
                    survived=int(d["survived"]),
                    survival_rate=float(d["survival_rate"]),
                    created_ts=str(d["created_ts"]),
                )
            )
        return tuple(results)


__all__ = ["DEFAULT_ROBUSTNESS_THRESHOLD", "AdversarialEvaluator", "AdversarialRunner"]
