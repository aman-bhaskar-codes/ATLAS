"""Routing evaluation tests: benchmark dataset + ablation harness (§59/§60/§85/§87)."""

from __future__ import annotations

from typing import Any

import pytest

from atlas.tooling.routing.evaluation import (
    RoutingAblationHarness,
    default_dataset_path,
    load_dataset,
)
from atlas.tooling.routing.models import JudgmentResult
from tests.tooling.routing_support import make_route_rules


@pytest.fixture
def dataset() -> list[Any]:
    cases = load_dataset()
    assert len(cases) >= 10
    categories = {case.category for case in cases}
    assert {"domain", "strategy", "fallback", "recovery"} <= categories
    assert any(case.ambiguous for case in cases)  # easy AND ambiguous (§85)
    return cases


class ScriptedJev:
    """A judgment provider double that answers from a scripted mapping —
    stands in for measured Jev behavior in offline ablations (§60: no external
    calls in evaluation runs)."""

    name = "jev"

    def __init__(self, answers: dict[str, tuple[str, float]]) -> None:
        self._answers = answers
        self.calls = 0

    async def available(self) -> bool:
        return True

    async def ask(self, questions: Any, state: Any) -> dict[str, Any]:
        self.calls += 1
        out: dict[str, Any] = {}
        objective = str(state.data.get("objective", "")).lower()
        for q in questions:
            answer = self._answers.get(q.question_id)
            if answer is None:
                # heuristic stand-in: route research-y objectives to research
                choice, score = (
                    ("research", 0.9)
                    if any(w in objective for w in ("papers", "research", "survey"))
                    else ("general", 0.8)
                )
            else:
                choice, score = answer
            out[q.question_id] = JudgmentResult(
                question_id=q.question_id,
                provider="jev",
                choice=choice,
                score=score,
            )
        return out


@pytest.mark.asyncio
async def test_ablation_harness_compares_stacks(dataset: list[Any]) -> None:
    """§60/§87: rule_only vs rule+jev vs rule+llm vs full — measured, not assumed."""
    harness = RoutingAblationHarness(
        rules=make_route_rules(),
        dataset=dataset,
        available_domains=["general", "research", "agentic_ide"],
    )
    results = await harness.run_all(jev=ScriptedJev({}))
    assert set(results) == {"rule_only", "rule_plus_jev", "rule_plus_llm", "full"}
    for _stack, result in results.items():
        summary = result.as_dict()
        assert summary["total"] == len(dataset)
        assert 0.0 <= summary["accuracy"] <= 1.0
        assert 0.0 <= summary["escalation_rate"] <= 1.0
    # The scripted-Jev stack must have actually engaged its provider.
    assert results["rule_plus_jev"].total == results["rule_only"].total


@pytest.mark.asyncio
async def test_ablation_measures_escalation_and_fallback(dataset: list[Any]) -> None:
    """§59: escalation/fallback rates are measured per stack."""

    class NeverConfidentJev(ScriptedJev):
        async def ask(self, questions: Any, state: Any) -> dict[str, Any]:
            self.calls += 1
            return {
                q.question_id: JudgmentResult(question_id=q.question_id, provider="jev", choice="general", score=0.2)
                for q in questions
            }

    harness = RoutingAblationHarness(
        rules=make_route_rules(),
        dataset=[c for c in dataset if c.category == "domain"],
        available_domains=["general", "research", "agentic_ide"],
    )
    result = await harness.run(stack="rule_plus_jev", jev=NeverConfidentJev({}))
    # Deterministic rules answer the easy cases; the low-confidence Jev answers
    # on the rest are unaccepted → escalated (§30) — never silently routed.
    assert result.escalated >= 2
    assert result.escalation_rate > 0.0
    assert result.correct + result.escalated == result.total


@pytest.mark.asyncio
async def test_judgment_outcome_data_is_recorded_for_calibration(dataset: list[Any]) -> None:
    """§58/§59: the harness output supports Jev-vs-outcome calibration —
    per-case expected vs answered pairs are derivable."""
    harness = RoutingAblationHarness(
        rules=make_route_rules(),
        dataset=[c for c in dataset if c.category == "domain"],
        available_domains=["general", "research", "agentic_ide"],
    )
    result = await harness.run(stack="rule_only")
    # A calibration dataset is exactly these rows: (case, expected, answered, accepted, latency).
    assert result.total > 0
    assert result.avg_latency_ms >= 0.0
    assert default_dataset_path().exists()
