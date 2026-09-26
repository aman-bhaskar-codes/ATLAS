"""Routing evaluation: benchmark dataset + ablation harness (§59/§60/§85/§87).

The dataset (§85) lives at ``eval/routing/routing_benchmark.json`` and covers
domain classification, strategy selection, candidate choice, fallback choice,
and recovery choice — easy AND ambiguous cases. The harness runs the SAME
cases against configurable judgment stacks (rule_only / rule+jev / rule+llm /
full) and measures correctness, escalation/fallback rate, and latency —
ablation is measurement, not assumption (§87).

Judgment providers are injected, so offline ablations use recorded or fake
providers; nothing here calls external services.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from atlas.tooling.routing.judgment import (
    DeterministicJudgmentProvider,
    JudgmentCascade,
    JudgmentQuestion,
    JudgmentState,
    JudgmentThresholdPolicy,
)
from atlas.tooling.routing.models import DecisionRisk


@dataclass(frozen=True)
class BenchmarkCase:
    case_id: str
    category: str  # domain | strategy | candidates | fallback | recovery
    objective: str
    expected: str  # expected choice (domain id / strategy id / candidate id / recovery action)
    ambiguous: bool = False


@dataclass
class AblationResult:
    stack: str  # rule_only | rule_plus_jev | rule_plus_llm | full
    total: int = 0
    correct: int = 0
    escalated: int = 0  # unaccepted judgment → safe default / ask_user
    fallback_used: int = 0
    total_latency_ms: float = 0.0

    @property
    def accuracy(self) -> float:
        return self.correct / self.total if self.total else 0.0

    @property
    def escalation_rate(self) -> float:
        return self.escalated / self.total if self.total else 0.0

    @property
    def fallback_rate(self) -> float:
        return self.fallback_used / self.total if self.total else 0.0

    @property
    def avg_latency_ms(self) -> float:
        return self.total_latency_ms / self.total if self.total else 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "stack": self.stack,
            "total": self.total,
            "accuracy": round(self.accuracy, 4),
            "escalation_rate": round(self.escalation_rate, 4),
            "fallback_rate": round(self.fallback_rate, 4),
            "avg_latency_ms": round(self.avg_latency_ms, 2),
        }


def default_dataset_path() -> Path:
    return Path(__file__).resolve().parents[4] / "eval" / "routing" / "routing_benchmark.json"


def load_dataset(path: Path | None = None) -> list[BenchmarkCase]:
    data = json.loads((path or default_dataset_path()).read_text())
    return [BenchmarkCase(**case) for case in data["cases"]]


class RoutingAblationHarness:
    """Runs the benchmark through configurable judgment stacks (§60/§87)."""

    def __init__(
        self,
        *,
        rules: dict[str, Any],
        dataset: list[BenchmarkCase],
        available_domains: list[str],
        risk: DecisionRisk = DecisionRisk.LOW_RISK_ROUTING,
        thresholds: JudgmentThresholdPolicy | None = None,
    ) -> None:
        self._rules = rules
        self._dataset = dataset
        self._available_domains = available_domains
        self._risk = risk
        self._thresholds = thresholds or JudgmentThresholdPolicy()

    async def run(
        self,
        *,
        stack: str,
        jev: Any = None,  # JudgmentProvider-like double or real JevJudgmentProvider
        llm: Any = None,
    ) -> AblationResult:
        cascade = JudgmentCascade(
            deterministic=DeterministicJudgmentProvider(rules=self._rules),
            jev=jev if stack in ("rule_plus_jev", "full") else None,
            llm=llm if stack in ("rule_plus_llm", "full") else None,
            thresholds=self._thresholds,
        )
        result = AblationResult(stack=stack)
        for case in self._dataset:
            started = time.perf_counter()
            question = JudgmentQuestion(
                question_id="domain" if case.category == "domain" else case.category,
                text=f"Which {case.category} matches: {case.objective}?",
                kind="choice",
                options=tuple(self._available_domains),
            )
            batch = await cascade.ask(
                (question,),
                JudgmentState(
                    state_id=case.case_id,
                    summary=case.objective,
                    data={"objective": case.objective},
                ),
                risk=self._risk,
            )
            elapsed = (time.perf_counter() - started) * 1000
            result.total += 1
            result.total_latency_ms += elapsed
            answer = batch.results[0] if batch.results else None
            if answer is None:
                continue
            if answer.provider in ("jev", "llm") and answer.fallback_used:
                result.fallback_used += 1
            if not answer.accepted:
                result.escalated += 1
                continue  # escalation → safe default counts as not-correct (measured, §59)
            if answer.choice == case.expected:
                result.correct += 1
        return result

    async def run_all(self, *, jev: Any = None, llm: Any = None) -> dict[str, AblationResult]:
        """§87: the five comparable stacks."""
        stacks = {
            "rule_only": await self.run(stack="rule_only"),
            "rule_plus_jev": await self.run(stack="rule_plus_jev", jev=jev),
            "rule_plus_llm": await self.run(stack="rule_plus_llm", llm=llm),
            "full": await self.run(stack="full", jev=jev, llm=llm),
        }
        return stacks


__all__ = [
    "AblationResult",
    "BenchmarkCase",
    "RoutingAblationHarness",
    "default_dataset_path",
    "load_dataset",
]
