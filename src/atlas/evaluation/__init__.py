"""Evaluation plane — measurable correctness before any 'self-improving' claim.

Layer position: between diagnostics and orchestration. It consumes memory
(trajectories) and intelligence (judge models) but nothing above it imports it
except interfaces (CLI/API) and diagnostics-free tests.
"""

from atlas.evaluation.evaluators import DeterministicEvaluator, Evaluator, LLMJudge
from atlas.evaluation.golden import GoldenTask, load_golden_suite
from atlas.evaluation.service import EvaluationService, RunReport

__all__ = [
    "DeterministicEvaluator",
    "EvaluationService",
    "Evaluator",
    "GoldenTask",
    "LLMJudge",
    "RunReport",
    "load_golden_suite",
]
