"""Evaluation store + service — run golden suites, persist results, gate regressions."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path

from atlas.evaluation.evaluators import DeterministicEvaluator, EvalResult, Evaluator, LLMJudge
from atlas.evaluation.golden import GoldenTask, load_golden_suite
from atlas.infra.clock import Clock
from atlas.infra.db import Database
from atlas.infra.ids import IdGenerator
from atlas.intelligence.gateway import ModelGateway


class EvaluationStore:
    """SQLite persistence for evaluation results (migration 14)."""

    def __init__(self, db: Database, ids: IdGenerator, clock: Clock) -> None:
        self._db = db
        self._ids = ids
        self._clock = clock

    async def save(self, *, golden_id: str, run_id: str, result: EvalResult, answer: str, latency_ms: int) -> str:
        row_id = self._ids.execution_id()
        detail = json.dumps(
            {
                "criteria": result.criteria,
                "failure_reason": result.failure_reason,
                "judge_rationale": result.judge_rationale,
            }
        )
        await self._db.conn.execute(
            "INSERT INTO evaluation_results "
            "(id, golden_id, run_id, evaluator, passed, score, detail, answer, latency_ms, created_ts) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                row_id,
                golden_id,
                run_id,
                result.evaluator,
                int(result.passed),
                result.score,
                detail,
                answer[:4000],
                latency_ms,
                self._clock.now().isoformat(),
            ),
        )
        await self._db.conn.commit()
        return row_id

    async def latest_run_passing(self, golden_id: str) -> bool | None:
        """Whether the most recent recorded result for a task passed.

        None when the task has never run (no baseline to regress from).
        """
        cur = await self._db.conn.execute(
            "SELECT passed FROM evaluation_results WHERE golden_id = ? ORDER BY created_ts DESC, rowid DESC LIMIT 1",
            (golden_id,),
        )
        row = await cur.fetchone()
        return None if row is None else bool(row["passed"])


@dataclass
class RunReport:
    run_id: str
    total: int = 0
    passed: int = 0
    failed: int = 0
    regressions: list[str] = field(default_factory=list)  # golden ids that previously passed
    results: dict[str, EvalResult] = field(default_factory=dict)

    @property
    def success_rate(self) -> float:
        return self.passed / self.total if self.total else 0.0

    @property
    def gate_passed(self) -> bool:
        return self.failed == 0 and not self.regressions


class EvaluationService:
    """Runs a golden suite against supplied answers and records results.

    Answers arrive from the caller (recorded fixtures in CI, live agent runs
    behind the gated suite) so evaluation itself stays deterministic.
    """

    def __init__(
        self,
        *,
        store: EvaluationStore,
        ids: IdGenerator,
        judge: LLMJudge | None = None,
        deterministic: Evaluator | None = None,
    ) -> None:
        self._store = store
        self._ids = ids
        self._judge = judge
        self._det = deterministic or DeterministicEvaluator()

    @staticmethod
    def load_suite(path: Path) -> tuple[GoldenTask, ...]:
        return load_golden_suite(path)

    async def run_suite(
        self,
        tasks: tuple[GoldenTask, ...],
        answers: dict[str, str],
        *,
        check_regression: bool = True,
    ) -> RunReport:
        run_id = self._ids.execution_id()
        report = RunReport(run_id=run_id)
        for task in tasks:
            # Baseline must be read BEFORE this run's result is inserted.
            baseline = (await self._store.latest_run_passing(task.id)) if check_regression else None
            answer = answers.get(task.id, "")
            started = time.perf_counter()
            result = await self._det.evaluate(task, answer)
            if task.use_llm_judge and self._judge is not None:
                judged = await self._judge.evaluate(task, answer)
                # Strict: both evaluators must pass.
                result = EvalResult(
                    passed=result.passed and judged.passed,
                    score=(result.score + judged.score) / 2,
                    evaluator="deterministic+llm_judge",
                    criteria={**result.criteria, **judged.criteria},
                    failure_reason=result.failure_reason or judged.failure_reason,
                    judge_rationale=judged.judge_rationale,
                )
            latency_ms = int((time.perf_counter() - started) * 1000)
            await self._store.save(
                golden_id=task.id,
                run_id=run_id,
                result=result,
                answer=answer,
                latency_ms=latency_ms,
            )
            report.total += 1
            report.results[task.id] = result
            if result.passed:
                report.passed += 1
            else:
                report.failed += 1
                # Baseline read before this run's insert: True means a
                # previously-passing task now fails → regression.
                if baseline is True:
                    report.regressions.append(task.id)
        return report


def build_evaluation_service(
    *,
    db: Database,
    ids: IdGenerator,
    clock: Clock,
    gateway: ModelGateway | None,
) -> EvaluationService:
    """Factory used by the composition root / CLI."""
    judge = LLMJudge(gateway) if gateway is not None else None
    return EvaluationService(
        store=EvaluationStore(db, ids, clock),
        ids=ids,
        judge=judge,
    )
