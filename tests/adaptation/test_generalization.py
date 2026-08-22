"""Tests for the generalization gate, adversarial evaluation, recovery and
long-horizon evaluation, the evaluation dataset and synthetic variants
(Prompt 4 §38-§44)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from atlas.adaptation import (
    AdversarialEvaluator,
    EvalDatasetStore,
    EvalSample,
    GeneralizationGate,
    LongHorizonEvaluator,
    PerturbationKind,
    RecoveryEvaluator,
    SyntheticGenerator,
    VariantKind,
)
from atlas.infra.db import Database
from atlas.memory.trajectory import (
    DecisionOutcome,
    DecisionPoint,
    DecisionTrace,
    FailureCategory,
    FailureRecord,
    Trajectory,
)


def _trajectory(tid: str, **overrides: object) -> Trajectory:
    now = datetime.now(UTC)
    base: dict[str, object] = {
        "id": tid,
        "task_id": f"task_{tid}",
        "correlation_id": "corr",
        "request": "research task",
        "goal": "summarize the topic",
        "plan_steps": ("search", "read", "summarize"),
        "risk_level": "low",
        "plan_confidence": 0.7,
        "success": True,
        "steps_taken": 3,
        "latency_ms": 50,
        "tokens_used": 10,
        "cost_usd": 0.001,
        "model_calls": 1,
        "tool_calls": 1,
        "created_ts": now,
        "completed_ts": now,
    }
    base.update(overrides)
    return Trajectory(**base)  # type: ignore[arg-type]


def _failure(tid: str, step: int) -> FailureRecord:
    return FailureRecord(
        id=f"fr_{tid}_{step}",
        task_id=f"task_{tid}",
        correlation_id="corr",
        ts=datetime.now(UTC),
        category=FailureCategory.TOOL_ERROR,
        step=step,
        component="browser",
        error_message="transient",
    )


class TestGeneralizationGate:
    async def test_benchmark_only_improvement_fails(self, memory_db: Database) -> None:
        """§39: benchmark improves but unseen collapses → not promotable."""
        gate = GeneralizationGate(db=memory_db)
        report = await gate.assess("exp1", in_domain=0.9, unseen=0.3)
        assert not report.gate_passed
        assert any("collapses" in r for r in report.reasons)
        saved = await gate.latest_for("exp1")
        assert saved is not None and not saved.gate_passed

    async def test_unseen_holds_passes(self, memory_db: Database) -> None:
        gate = GeneralizationGate(db=memory_db)
        report = await gate.assess("exp2", in_domain=0.8, unseen=0.75, transfer=0.7, robustness=0.8)
        assert report.gate_passed
        assert report.reasons == ()

    async def test_absolute_floor_blocks(self, memory_db: Database) -> None:
        gate = GeneralizationGate(db=memory_db)
        # unseen 0.05 is above collapse ratio vs a tiny in_domain but below floor
        report = await gate.assess("exp3", in_domain=0.08, unseen=0.05)
        assert not report.gate_passed


class TestRecoveryEvaluation:
    async def test_clean_run_scores_full(self, memory_db: Database) -> None:
        evaluation = await RecoveryEvaluator(db=memory_db).evaluate(_trajectory("t1"), ())
        assert evaluation.score == 1.0
        assert not evaluation.initial_failure

    async def test_intelligent_recovery_scores_well(self, memory_db: Database) -> None:
        """§41: failing initially but recovering intelligently is good."""
        evaluation = await RecoveryEvaluator(db=memory_db).evaluate(
            _trajectory("t2", steps_taken=6), (_failure("t2", 2),), quality_after_recovery=0.9
        )
        assert evaluation.initial_failure and evaluation.recovered
        assert evaluation.recovery_steps == 4
        assert 0.9 <= evaluation.score <= 1.0

    async def test_unrecovered_failure_scores_low(self, memory_db: Database) -> None:
        evaluation = await RecoveryEvaluator(db=memory_db).evaluate(
            _trajectory("t3", success=False), (_failure("t3", 1),)
        )
        assert evaluation.initial_failure and not evaluation.recovered
        assert evaluation.score == 0.1


class TestLongHorizonEvaluation:
    async def test_short_tasks_are_not_long_horizon(self, memory_db: Database) -> None:
        result = await LongHorizonEvaluator(db=memory_db).evaluate(_trajectory("t1"), (), ())
        assert result is None  # 3 steps < minimum

    async def test_long_successful_run_scores_high(self, memory_db: Database) -> None:
        traces = (
            DecisionTrace(
                id="dt1",
                task_id="task_t4",
                correlation_id="corr",
                ts=datetime.now(UTC),
                decision_point=DecisionPoint.VERIFICATION,
                options_considered=("full", "quick"),
                chosen_option="full",
                rationale="test",
                outcome=DecisionOutcome.SUCCESS,
            ),
        )
        result = await LongHorizonEvaluator(db=memory_db).evaluate(
            _trajectory("t4", steps_taken=10), traces, (_failure("t4", 3), _failure("t4", 5))
        )
        assert result is not None
        assert result.steps == 10
        assert result.goal_completion == 1.0
        assert result.error_accumulation == pytest.approx(0.2)
        assert result.recovery == 1.0  # failed but completed
        assert result.score > 0.7


class TestAdversarialEvaluation:
    class Runner:
        def __init__(self, *, fail_perturbations: frozenset[PerturbationKind]) -> None:
            self._fail = fail_perturbations

        async def run(
            self, strategy_id: str, perturbation: PerturbationKind, tasks: tuple[str, ...]
        ) -> tuple[bool, ...]:
            if perturbation in self._fail:
                return (True, False, False, False, True)  # 2/5 survive
            return tuple(True for _ in tasks)

    async def test_survival_rates_persisted(self, memory_db: Database) -> None:
        evaluator = AdversarialEvaluator(
            db=memory_db, runner=self.Runner(fail_perturbations=frozenset({PerturbationKind.PROMPT_INJECTION}))
        )
        tasks = tuple(f"task_{i}" for i in range(5))
        ok = await evaluator.evaluate("s1", PerturbationKind.AMBIGUITY, tasks)
        assert ok.survival_rate == 1.0 and evaluator.is_robust(ok)
        bad = await evaluator.evaluate("s1", PerturbationKind.PROMPT_INJECTION, tasks)
        assert bad.survival_rate == pytest.approx(0.4) and not evaluator.is_robust(bad)
        saved = await evaluator.for_strategy("s1")
        assert len(saved) == 2

    async def test_full_catalogue(self, memory_db: Database) -> None:
        evaluator = AdversarialEvaluator(db=memory_db, runner=self.Runner(fail_perturbations=frozenset()))
        results = await evaluator.evaluate_all("s1", ("t1", "t2"))
        assert len(results) == len(PerturbationKind)  # all 10 perturbation classes

    async def test_no_tasks_no_fake_results(self, memory_db: Database) -> None:
        evaluator = AdversarialEvaluator(db=memory_db, runner=self.Runner(fail_perturbations=frozenset()))
        with pytest.raises(ValueError, match="at least one task"):
            await evaluator.evaluate("s1", PerturbationKind.AMBIGUITY, ())


class TestEvaluationDataset:
    async def test_sample_roundtrip(self, memory_db: Database) -> None:
        store = EvalDatasetStore(db=memory_db)
        sample = EvalSample(
            task="summarize a research paper",
            domain="research",
            difficulty="medium",
            success_criteria="accurate 5-sentence summary",
            allowed_capabilities=("knowledge",),
            risk="low",
        )
        await store.save(sample)
        fetched = await store.get(sample.sample_id)
        assert fetched is not None
        assert fetched.task == sample.task
        assert fetched.allowed_capabilities == ("knowledge",)
        assert (await store.samples())[0].sample_id == sample.sample_id

    async def test_unapproved_samples_filtered(self, memory_db: Database) -> None:
        store = EvalDatasetStore(db=memory_db)
        await store.save(EvalSample(task="a", approved=True))
        await store.save(EvalSample(task="b", approved=False, source="synthetic"))
        assert len(await store.samples()) == 1
        assert len(await store.samples(approved_only=False)) == 2


class TestSyntheticGeneration:
    async def test_variant_lifecycle_requires_human_review(self, memory_db: Database) -> None:
        """§44: variants are DRAFT until a human promotes them to GOLDEN."""
        generator = SyntheticGenerator(db=memory_db)
        sample = EvalSample(task="book a meeting")
        variant = await generator.generate(sample, VariantKind.PARAPHRASE)
        assert variant.status == "DRAFT"
        assert "different wording" in variant.task
        assert await generator.golden_variants() == ()

        approved = await generator.review(variant.variant_id, "APPROVED")
        assert approved.status == "APPROVED"
        golden = await generator.review(variant.variant_id, "GOLDEN")
        assert golden.status == "GOLDEN"
        assert len(await generator.golden_variants()) == 1

    async def test_finalized_variants_cannot_change(self, memory_db: Database) -> None:
        generator = SyntheticGenerator(db=memory_db)
        variant = await generator.generate(EvalSample(task="x"), VariantKind.DIFFERENT_DATA)
        await generator.review(variant.variant_id, "REJECTED")
        with pytest.raises(ValueError, match="finalized"):
            await generator.review(variant.variant_id, "GOLDEN")

    async def test_invalid_review_status_rejected(self, memory_db: Database) -> None:
        generator = SyntheticGenerator(db=memory_db)
        variant = await generator.generate(EvalSample(task="x"), VariantKind.DIFFERENT_FILES)
        with pytest.raises(ValueError, match="APPROVED"):
            await generator.review(variant.variant_id, "SOMETHING_ELSE")
