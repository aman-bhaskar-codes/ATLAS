"""Evaluation plane tests — golden loading, evaluators, service, regression gate."""

from __future__ import annotations

from pathlib import Path

import pytest

from atlas.evaluation.evaluators import DeterministicEvaluator, EvalResult, LLMJudge
from atlas.evaluation.golden import GoldenTask, MatchSpec, load_golden_suite
from atlas.evaluation.service import EvaluationService, EvaluationStore
from atlas.infra.clock import SystemClock
from atlas.infra.db import Database
from atlas.infra.ids import UuidGenerator

GOLDEN_DIR = Path(__file__).resolve().parents[2] / "eval" / "golden_tasks"


@pytest.fixture
async def db(tmp_path):  # type: ignore[no-untyped-def]
    d = Database(tmp_path / "eval.db")
    await d.start()
    yield d
    await d.stop()


def _task(**kwargs: object) -> GoldenTask:
    defaults: dict[str, object] = {
        "id": "t1",
        "category": "analysis",
        "prompt": "Add 2+2",
        "expected": MatchSpec(contains_all=["4"]),
    }
    defaults.update(kwargs)
    return GoldenTask(**defaults)  # type: ignore[arg-type]


class TestGoldenSuite:
    def test_loads_core_suite(self) -> None:
        suite = load_golden_suite(GOLDEN_DIR / "core.yaml")
        assert len(suite) >= 8
        assert all(isinstance(t, GoldenTask) for t in suite)
        ids = [t.id for t in suite]
        assert len(ids) == len(set(ids))

    def test_rejects_duplicate_ids(self, tmp_path: Path) -> None:
        f = tmp_path / "dups.yaml"
        f.write_text("- {id: a, category: x, prompt: p}\n- {id: a, category: x, prompt: q}\n")
        with pytest.raises(ValueError, match="duplicate"):
            load_golden_suite(f)

    def test_rejects_non_list(self, tmp_path: Path) -> None:
        f = tmp_path / "bad.yaml"
        f.write_text("id: a\n")
        with pytest.raises(ValueError, match="list"):
            load_golden_suite(f)


class TestDeterministicEvaluator:
    async def test_contains_all_pass(self) -> None:
        ev = DeterministicEvaluator()
        res = await ev.evaluate(_task(), "The answer is 4 (four)")
        assert res.passed and res.score == 1.0

    async def test_missing_content_fails(self) -> None:
        ev = DeterministicEvaluator()
        res = await ev.evaluate(_task(), "The answer is five")
        assert not res.passed
        assert "missing required content" in (res.failure_reason or "")

    async def test_forbidden_content_fails(self) -> None:
        ev = DeterministicEvaluator()
        task = _task(expected=MatchSpec(contains_all=["4"], contains_none=["sk-"]))
        res = await ev.evaluate(task, "4, and by the way sk-abc123 leaked")
        assert not res.passed
        assert "forbidden" in (res.failure_reason or "")

    async def test_contains_any_and_regex(self) -> None:
        ev = DeterministicEvaluator()
        task = _task(expected=MatchSpec(contains_any=["python", "rust"], regex_all=[r"\d{4}"]))
        res = await ev.evaluate(task, "Use python, released 2024")
        assert res.passed
        res2 = await ev.evaluate(task, "Use java, released long ago")
        assert not res2.passed

    async def test_length_bounds(self) -> None:
        ev = DeterministicEvaluator()
        task = _task(expected=MatchSpec(min_length=10, max_length=20))
        assert (await ev.evaluate(task, "0123456789")).passed
        assert not (await ev.evaluate(task, "short")).passed
        assert not (await ev.evaluate(task, "0123456789012345678901234567890")).passed


class TestLLMJudge:
    async def test_judge_error_fails_closed(self) -> None:
        class _BrokenGateway:
            async def complete(self, req: object) -> object:
                raise RuntimeError("provider down")

        judge = LLMJudge(_BrokenGateway())  # type: ignore[arg-type]
        res = await judge.evaluate(_task(), "4")
        assert not res.passed
        assert res.failure_reason and res.failure_reason.startswith("judge_error")

    async def test_judge_parses_valid_json(self) -> None:
        class _FakeResp:
            text = '{"passed": true, "score": 0.9, "criteria": {"correct": true}, "reason": "ok"}'

        class _FakeGateway:
            async def complete(self, req: object) -> object:
                return _FakeResp()

        judge = LLMJudge(_FakeGateway())  # type: ignore[arg-type]
        res = await judge.evaluate(_task(), "The answer is 4")
        assert res.passed and 0.85 < res.score <= 1.0
        assert res.judge_rationale == "ok"


class TestEvaluationService:
    async def test_run_suite_and_report(self, db: Database) -> None:
        store = EvaluationStore(db, UuidGenerator(), SystemClock())
        svc = EvaluationService(store=store, ids=UuidGenerator())
        tasks = (
            _task(id="a", expected=MatchSpec(contains_all=["4"])),
            _task(id="b", expected=MatchSpec(contains_all=["hidden"])),
        )
        report = await svc.run_suite(tasks, {"a": "it is 4", "b": "no match here"})
        assert report.total == 2
        assert report.passed == 1 and report.failed == 1
        assert report.gate_passed is False
        # No prior baseline → no regressions recorded, just failures.
        assert report.regressions == []

    async def test_regression_detection(self, db: Database) -> None:
        store = EvaluationStore(db, UuidGenerator(), SystemClock())
        svc = EvaluationService(store=store, ids=UuidGenerator())
        tasks = (_task(id="a", expected=MatchSpec(contains_all=["4"])),)

        # Run 1: passes → baseline established.
        r1 = await svc.run_suite(tasks, {"a": "4"})
        assert r1.gate_passed

        # Run 2: fails → regression flagged.
        r2 = await svc.run_suite(tasks, {"a": "not four"})
        assert not r2.gate_passed
        assert "a" in r2.regressions

    async def test_results_persisted(self, db: Database) -> None:
        store = EvaluationStore(db, UuidGenerator(), SystemClock())
        await store.save(
            golden_id="g",
            run_id="r",
            result=EvalResult(passed=True, score=1.0, evaluator="deterministic"),
            answer="ans",
            latency_ms=5,
        )
        cur = await db.conn.execute("SELECT golden_id, passed FROM evaluation_results")
        rows = await cur.fetchall()
        assert len(rows) == 1 and rows[0]["golden_id"] == "g" and rows[0]["passed"] == 1
        assert await store.latest_run_passing("g") is True
        assert await store.latest_run_passing("never-run") is None
