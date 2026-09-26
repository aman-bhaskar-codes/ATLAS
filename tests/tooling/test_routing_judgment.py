"""Judgment layer tests (Part 3 §22-§32/§61): providers, thresholds, cascade."""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from atlas.tooling.routing.judgment import (
    DeterministicJudgmentProvider,
    JevClient,
    JevJudgmentProvider,
    JevSchemaError,
    JevUnavailableError,
    JudgmentCascade,
    JudgmentQuestion,
    JudgmentState,
    JudgmentThresholdPolicy,
    LLMJudgmentProvider,
)
from atlas.tooling.routing.models import DecisionRisk

QUESTIONS = (
    JudgmentQuestion("domain", "Which domain best matches?", "choice", ("research", "general")),
    JudgmentQuestion("needs_research", "Is deep research required?", "score"),
    JudgmentQuestion("needs_human", "Should the workflow escalate?", "score"),
)
STATE = JudgmentState(state_id="c1", summary="objective: test", data={})


class FakeJevResponse:
    def __init__(self, status_code: int, body: Any) -> None:
        self.status_code = status_code
        self._body = body

    def json(self) -> Any:
        return self._body


def test_threshold_policy_varies_by_decision_risk() -> None:
    """§29: no single global threshold; DESTRUCTIVE never routes on judgment."""
    policy = JudgmentThresholdPolicy()
    assert policy.threshold(DecisionRisk.LOW_RISK_ROUTING) < policy.threshold(DecisionRisk.MEDIUM_RISK)
    assert policy.threshold(DecisionRisk.MEDIUM_RISK) < policy.threshold(DecisionRisk.HIGH_RISK)
    accepted, _ = policy.evaluate(0.95, DecisionRisk.DESTRUCTIVE)
    assert accepted is False  # 0.99 bar — judgment alone never authorizes destruction


def test_confidence_states_normalized_raw_score_retained() -> None:
    """§30: HIGH/MEDIUM/LOW normalization, raw score kept in the result."""
    accepted, level = JudgmentThresholdPolicy().evaluate(0.9, DecisionRisk.MEDIUM_RISK)
    assert accepted is True and level.value == "HIGH_CONFIDENCE"
    _, low = JudgmentThresholdPolicy().evaluate(0.3, DecisionRisk.MEDIUM_RISK)
    assert low.value == "LOW_CONFIDENCE"


def test_jev_client_unconfigured_is_unavailable() -> None:
    client = JevClient(api_key="")
    assert client.is_configured() is False
    provider = JevJudgmentProvider(client)
    import asyncio

    assert asyncio.run(provider.available()) is False


@pytest.mark.asyncio
async def test_jev_client_parses_batched_choice_and_score(monkeypatch: Any) -> None:
    """§23/§26: one call answers multiple questions (choice + score)."""
    client = JevClient(api_key="test-key")
    calls: list[dict[str, Any]] = []

    async def fake_post(self: Any, url: str, **kwargs: Any) -> FakeJevResponse:
        calls.append(kwargs["json"])
        return FakeJevResponse(
            200,
            {
                "results": [
                    {
                        "id": "domain",
                        "choice": "research",
                        "score": 0.91,
                        "probabilities": {"research": 0.91, "general": 0.09},
                    },
                    {"id": "needs_research", "score": 0.87},
                ]
            },
        )

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)
    results = await client.ask(QUESTIONS, STATE)

    assert len(calls) == 1  # ONE call for three questions (§26)
    assert calls[0]["model"] == "systemone"
    assert len(calls[0]["questions"]) == 3
    assert results["domain"].choice == "research"
    assert results["domain"].score == pytest.approx(0.91)
    assert results["domain"].raw_probabilities == {"research": 0.91, "general": 0.09}  # §57 telemetry
    assert "needs_human" not in results  # unanswered questions fall through the ladder


@pytest.mark.asyncio
async def test_jev_client_retries_on_5xx_then_raises(monkeypatch: Any) -> None:
    client = JevClient(api_key="test-key", max_attempts=2)
    attempts = 0

    async def fake_post(self: Any, url: str, **kwargs: Any) -> FakeJevResponse:
        nonlocal attempts
        attempts += 1
        return FakeJevResponse(503, {})

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)
    with pytest.raises(JevUnavailableError):
        await client.ask(QUESTIONS, STATE)
    assert attempts == 2  # bounded retry (§24)


@pytest.mark.asyncio
async def test_jev_client_rejects_garbage_schema(monkeypatch: Any) -> None:
    """§24: response validation fails loudly so the cascade can fall back."""
    client = JevClient(api_key="test-key", max_attempts=1)

    async def fake_post(self: Any, url: str, **kwargs: Any) -> FakeJevResponse:
        return FakeJevResponse(200, {"unexpected": "shape"})

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)
    with pytest.raises(JevSchemaError):
        await client.ask(QUESTIONS, STATE)


@pytest.mark.asyncio
async def test_cascade_prefers_deterministic_rules_over_jev() -> None:
    """§61: an exact rule answers; Jev is never called for it."""
    jev_calls = 0

    class CountingJev:
        name = "jev"

        async def available(self) -> bool:
            return True

        async def ask(self, questions: Any, state: Any) -> dict[str, Any]:
            nonlocal jev_calls
            jev_calls += 1
            return {}

    cascade = JudgmentCascade(
        deterministic=DeterministicJudgmentProvider(
            rules={"domain": lambda state: ("research", 1.0)},
        ),
        jev=CountingJev(),
    )
    batch = await cascade.ask(QUESTIONS[:1], STATE)
    assert batch.results[0].choice == "research"
    assert batch.results[0].provider == "deterministic"
    assert jev_calls == 0
    assert batch.provider_chain == ("deterministic",)


@pytest.mark.asyncio
async def test_cascade_falls_back_when_jev_fails() -> None:
    """§32: Jev unavailable → route continues; unanswered → unaccepted."""
    cascade = JudgmentCascade(
        deterministic=DeterministicJudgmentProvider(rules={}),  # no rules
        jev=JevJudgmentProvider(JevClient(api_key="")),  # unconfigured
    )
    batch = await cascade.ask(QUESTIONS, STATE, risk=DecisionRisk.MEDIUM_RISK)
    assert "jev" not in batch.provider_chain
    assert all(r.accepted is False for r in batch.results)
    assert all(r.fallback_used for r in batch.results)
    assert all(r.provider == "none" for r in batch.results)


@pytest.mark.asyncio
async def test_cascade_low_confidence_is_not_accepted() -> None:
    """§30: low confidence → stronger model / clarification, never auto-route."""

    class LowConfidenceJev:
        name = "jev"

        async def available(self) -> bool:
            return True

        async def ask(self, questions: Any, state: Any) -> dict[str, Any]:
            return {q.question_id: await self._answer(q) for q in questions}

        async def _answer(self, q: Any) -> Any:
            from atlas.tooling.routing.judgment import JudgmentResult

            return JudgmentResult(question_id=q.question_id, provider="jev", choice="research", score=0.4)

    cascade = JudgmentCascade(deterministic=DeterministicJudgmentProvider(), jev=LowConfidenceJev())
    batch = await cascade.ask(QUESTIONS[:1], STATE, risk=DecisionRisk.LOW_RISK_ROUTING)
    assert batch.results[0].accepted is False
    assert batch.results[0].score == pytest.approx(0.4)  # raw retained (§30)


@pytest.mark.asyncio
async def test_llm_judgment_provider_parses_gateway_response() -> None:
    class FakeGateway:
        async def complete(self, request: Any) -> Any:
            from atlas.infra.types import ModelResponse, ModelTarget

            return ModelResponse(
                text='{"answers": [{"id": "domain", "choice": "research", "confidence": 0.88}]}',
                target=ModelTarget.LOCAL_FAST,
                model="fake",
            )

    provider = LLMJudgmentProvider(FakeGateway())
    results = await provider.ask(QUESTIONS[:1], STATE)
    assert results["domain"].choice == "research"
    assert results["domain"].provider == "llm"
