"""Evaluation plane — measurable correctness before any 'self-improving' claim.

Layer position: between diagnostics and orchestration. It consumes memory
(trajectories) and intelligence (judge models) but nothing above it imports it
except interfaces (CLI/API) and diagnostics-free tests.

The Knowledge Fabric adds an OFFLINE rag-evaluation layer on top
(§59-66, §99-110): `atlas.evaluation.rag_metrics`, `rag_datasets`, and
`rag_experiments` — deterministic Ragas-style metrics, datasets, and
baseline-vs-candidate gates. They never run on the hot path (§130).
"""

from atlas.evaluation.evaluators import DeterministicEvaluator, Evaluator, LLMJudge
from atlas.evaluation.golden import GoldenTask, load_golden_suite
from atlas.evaluation.rag_datasets import EvalDataset, EvalEntry, builtin_smoke_dataset, load_jsonl
from atlas.evaluation.rag_experiments import ExperimentResult, RegressionGate, VariantConfig, run_experiment
from atlas.evaluation.rag_metrics import (
    answer_relevancy,
    context_precision,
    context_recall,
    evaluate_answer,
    faithfulness,
)
from atlas.evaluation.service import EvaluationService, RunReport

__all__ = [
    "DeterministicEvaluator",
    "EvalDataset",
    "EvalEntry",
    "EvaluationService",
    "Evaluator",
    "ExperimentResult",
    "GoldenTask",
    "LLMJudge",
    "RegressionGate",
    "RunReport",
    "VariantConfig",
    "answer_relevancy",
    "builtin_smoke_dataset",
    "context_precision",
    "context_recall",
    "evaluate_answer",
    "faithfulness",
    "load_golden_suite",
    "load_jsonl",
    "run_experiment",
]
