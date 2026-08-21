"""RAG evaluation layer tests (§59-66, §107-110): metrics, datasets, experiments, gate."""

from __future__ import annotations

import json
from pathlib import Path

from atlas.evaluation.rag_datasets import EvalDataset, EvalEntry, builtin_smoke_dataset, load_jsonl
from atlas.evaluation.rag_experiments import ExperimentResult, RegressionGate, run_experiment
from atlas.evaluation.rag_metrics import (
    answer_relevancy,
    context_precision,
    context_recall,
    evaluate_answer,
    faithfulness,
)
from atlas.knowledge.domain import Claim, ClaimStatus, Evidence, QueryRoute, RAGMode, SourceType
from atlas.knowledge.synthesis import FabricAnswer
from tests.knowledge.harness import NOW


def _claim(status: ClaimStatus) -> Claim:
    return Claim(claim_id=f"c_{status.value}", text="a claim", status=status)


def _answer(*, answered: bool, text: str, quote: str = "", supported: int = 0, unsupported: int = 0) -> FabricAnswer:
    evidence = ()
    if quote:
        evidence = (
            Evidence(
                evidence_id="ev_1",
                document_id="doc_1",
                chunk_id="chk_1",
                source=SourceType.LOCAL_FILE,
                quote=quote,
                retrieved_at=NOW,
            ),
        )
    claims = [_claim(ClaimStatus.SUPPORTED)] * supported + [_claim(ClaimStatus.UNSUPPORTED)] * unsupported
    return FabricAnswer(
        query="q",
        mode=RAGMode.RAG,
        route=QueryRoute.MIXED,
        text=text,
        answered=answered,
        confidence=0.8 if answered else 0.05,
        evidence=evidence,
        claims=tuple(claims),
    )


# ── metrics ─────────────────────────────────────────────────────────────
def test_faithfulness_is_fraction_of_supported_claims() -> None:
    assert faithfulness([]) == 1.0
    assert faithfulness([_claim(ClaimStatus.SUPPORTED)]) == 1.0
    assert faithfulness([_claim(ClaimStatus.SUPPORTED), _claim(ClaimStatus.UNSUPPORTED)]) == 0.5


def test_answer_relevancy_measures_token_overlap() -> None:
    assert answer_relevancy("the steam engine improved efficiency", "steam engine efficiency") > 0.5
    assert answer_relevancy("cakes and frosting recipes", "how does retrieval work?") == 0.0
    assert answer_relevancy("", "anything") == 0.0


def test_context_precision_scores_retrieved_contexts() -> None:
    assert context_precision("steam engine history", ["the steam engine history begins here"]) > 0.5
    assert context_precision("steam engine history", ["unrelated baking notes"]) == 0.0
    assert context_precision("steam", []) == 0.0


def test_context_recall_covers_ground_truth() -> None:
    gt = "Reciprocal rank fusion combines ranked lists"
    assert context_recall(gt, ["reciprocal rank fusion combines multiple ranked lists robustly"]) == 1.0
    assert context_recall(gt, []) == 0.0
    assert context_recall("", []) == 1.0


def test_evaluate_answer_builds_a_metrics_row() -> None:
    row = evaluate_answer(
        answer="the engine was invented in 1712",
        query="when was the engine invented",
        contexts=["the engine was invented in 1712"],
        claims=[_claim(ClaimStatus.SUPPORTED)],
        ground_truth="invented in 1712",
    )
    assert set(row) == {"faithfulness", "answer_relevancy", "context_precision", "context_recall"}
    assert row["faithfulness"] == 1.0
    without_gt = evaluate_answer(answer="a", query="b", contexts=[], claims=[])
    assert "context_recall" not in without_gt


# ── datasets ────────────────────────────────────────────────────────────
def test_builtin_smoke_dataset_covers_main_categories() -> None:
    ds = builtin_smoke_dataset()
    assert ds.name == "builtin_smoke"
    assert len(ds.entries) == 4
    assert ds.by_category("unanswerable")
    assert ds.by_category("codebase")


def test_load_jsonl_roundtrips_entries(tmp_path: Path) -> None:
    p = tmp_path / "ds.jsonl"
    rows = [
        {"query": "q1", "ground_truth": "gt1", "expected_uris": ["u1"], "category": "simple_fact"},
        {"query": "q2"},
    ]
    p.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    ds = load_jsonl(p)
    assert ds.name == "ds"
    assert ds.entries[0].expected_uris == ("u1",)
    assert ds.entries[1].category == "general"


# ── experiments + regression gate ───────────────────────────────────────
async def test_run_experiment_averages_metrics_and_tracks_answer_rate() -> None:
    async def query_fn(query: str) -> FabricAnswer:
        if "unanswerable" in query:
            return _answer(answered=False, text="")
        return _answer(
            answered=True,
            text=f"{query} was built in 1712",
            quote=f"evidence about {query} built in 1712",
            supported=1,
        )

    dataset = EvalDataset(
        name="mini",
        entries=[
            EvalEntry(query="when was the engine built", ground_truth="built in 1712", category="simple_fact"),
            EvalEntry(query="unanswerable mystery topic", category="unanswerable"),
        ],
    )
    result = await run_experiment(query_fn, dataset, variant="baseline")
    assert result.variant == "baseline"
    assert result.answered_rate == 0.5
    assert result.metrics["faithfulness"] == 1.0  # answered claim supported; refusal has no claims
    assert len(result.per_query) == 2
    assert result.summary()["variant"] == "baseline"


def _exp(metrics: dict[str, float]) -> ExperimentResult:
    return ExperimentResult(variant="v", dataset="d", metrics=metrics, per_query=(), answered_rate=1.0)


def test_regression_gate_passes_on_improvement() -> None:
    gate = RegressionGate(primary_metric="faithfulness", min_delta=0.0, max_regression=0.05)
    baseline = _exp({"faithfulness": 0.7, "answer_relevancy": 0.6})
    candidate = _exp({"faithfulness": 0.8, "answer_relevancy": 0.58})
    passed, deltas = gate.check(baseline, candidate)
    assert passed is True
    assert deltas["faithfulness"] == 0.1


def test_regression_gate_fails_on_primary_regression() -> None:
    gate = RegressionGate()
    baseline = _exp({"faithfulness": 0.7})
    candidate = _exp({"faithfulness": 0.6})
    passed, _ = gate.check(baseline, candidate)
    assert passed is False


def test_regression_gate_fails_on_secondary_collapse() -> None:
    gate = RegressionGate(primary_metric="faithfulness", max_regression=0.05)
    baseline = _exp({"faithfulness": 0.7, "answer_relevancy": 0.6})
    candidate = _exp({"faithfulness": 0.9, "answer_relevancy": 0.4})  # relevancy regressed 0.2
    passed, deltas = gate.check(baseline, candidate)
    assert passed is False
    assert deltas["answer_relevancy"] == -0.2
