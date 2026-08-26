"""Baseline-vs-candidate experiments and the regression gate (§65-66, §108-110).

A/B policy (§128): a retrieval/rerank/chunker variant is ONLY promoted after
an experiment on a frozen dataset shows it beats the baseline. Experiments
run OFFLINE — the fabric's hot path never imports or calls this module.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from atlas.evaluation.rag_datasets import EvalDataset
from atlas.evaluation.rag_metrics import evaluate_answer
from atlas.knowledge.synthesis import FabricAnswer


class QueryFn(Protocol):
    """Any fabric (or fabric variant) that answers a query."""

    async def __call__(self, query: str) -> FabricAnswer: ...


@dataclass(frozen=True)
class VariantConfig:
    name: str
    description: str = ""


@dataclass(frozen=True)
class ExperimentResult:
    variant: str
    dataset: str
    metrics: dict[str, float]  # macro-averaged over entries
    per_query: tuple[dict[str, Any], ...]
    answered_rate: float

    def summary(self) -> dict[str, Any]:
        return {"variant": self.variant, "dataset": self.dataset, **self.metrics, "answered_rate": self.answered_rate}


async def run_experiment(query_fn: QueryFn, dataset: EvalDataset, *, variant: str = "baseline") -> ExperimentResult:
    rows: list[dict[str, Any]] = []
    answered = 0
    for entry in dataset.entries:
        answer = await query_fn(entry.query)
        contexts = [ev.quote for ev in answer.evidence]
        row: dict[str, Any] = evaluate_answer(
            answer=answer.text,
            query=entry.query,
            contexts=contexts,
            claims=list(answer.claims),
            ground_truth=entry.ground_truth,
        )
        row["query"] = entry.query
        row["answered"] = answer.answered
        row["category"] = entry.category
        rows.append(row)
        answered += 1 if answer.answered else 0

    metrics: dict[str, float] = {}
    keys = ("faithfulness", "answer_relevancy", "context_precision", "context_recall")
    for key in keys:
        values = [r[key] for r in rows if key in r]
        if values:
            metrics[key] = round(sum(values) / len(values), 3)
    return ExperimentResult(
        variant=variant,
        dataset=dataset.name,
        metrics=metrics,
        per_query=tuple(rows),
        answered_rate=round(answered / len(dataset.entries), 3) if dataset.entries else 0.0,
    )


@dataclass(frozen=True)
class RegressionGate:
    """Candidate must beat baseline on the primary metric by min_delta AND
    not regress any secondary metric beyond max_regression (§110)."""

    primary_metric: str = "faithfulness"
    min_delta: float = 0.0
    max_regression: float = 0.05

    def check(self, baseline: ExperimentResult, candidate: ExperimentResult) -> tuple[bool, dict[str, float]]:
        deltas = {
            key: round(candidate.metrics.get(key, 0.0) - baseline.metrics.get(key, 0.0), 3) for key in baseline.metrics
        }
        primary_ok = deltas.get(self.primary_metric, 0.0) >= self.min_delta
        no_regression = all(v >= -self.max_regression for v in deltas.values())
        return primary_ok and no_regression, deltas
