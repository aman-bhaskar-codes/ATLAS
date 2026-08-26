"""Phase 2 understanding tests — request text becomes a typed TaskIntent.

The point of these tests is the *deterministic floors*. A model call can
degrade or return garbage; when it does, the intent must fail toward more
caution and more reasoning, never less. That behaviour is what keeps a
mislabelled destructive request out of Tier 0 auto-approval.
"""

from __future__ import annotations

from typing import Any

import pytest

from atlas.infra.cognition import (
    Complexity,
    ReasoningLevel,
    RiskLevel,
    TaskDomain,
    TaskIntent,
)
from atlas.orchestration.understanding import (
    IntentExtractor,
    capabilities_from_intent,
    select_reasoning_level,
)

_CORR: Any = "corr-1"


class _FakeGateway:
    def __init__(self, responses: list[str]) -> None:
        self._responses = list(responses)
        self.calls: list[Any] = []

    async def complete(self, req: Any) -> Any:
        self.calls.append(req)
        text = self._responses.pop(0) if self._responses else "{}"

        class _Resp:
            cost = type("c", (), {"input_tokens": 10, "output_tokens": 5})()

        _Resp.text = text  # type: ignore[attr-defined]
        return _Resp()


def _extractor(responses: list[str]) -> tuple[IntentExtractor, _FakeGateway]:
    gw = _FakeGateway(responses)
    return IntentExtractor(gw), gw  # type: ignore[arg-type]


_FULL = (
    '{"objective":"Delete the temp cache directory",'
    '"domain":"filesystem","constraints":["do not touch src"],'
    '"success_criteria":["directory no longer exists"],'
    '"risk":"high","privacy_level":"internal","urgency":"normal",'
    '"complexity":"simple","required_capabilities":["filesystem"],'
    '"likely_side_effects":["removes files from disk"],'
    '"verification_requirements":["stat the path"],"confidence":0.9}'
)


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_understand_parses_full_intent() -> None:
    ex, _ = _extractor([_FULL])
    intent = await ex.understand("delete the temp cache dir", _CORR)
    assert intent.objective == "Delete the temp cache directory"
    assert intent.domain is TaskDomain.FILESYSTEM
    assert intent.risk is RiskLevel.HIGH
    assert intent.success_criteria == ("directory no longer exists",)
    assert intent.likely_side_effects == ("removes files from disk",)
    assert intent.required_capabilities == ("filesystem",)
    assert intent.confidence == pytest.approx(0.9, abs=0.01)
    # HIGH risk => L3, per select_reasoning_level
    assert intent.reasoning_level is ReasoningLevel.L3_DEEP


@pytest.mark.asyncio
async def test_understand_uses_the_fast_tier() -> None:
    """Phase 4 assigns structured extraction to the fast model. This runs on
    every single task, so putting it on the deep tier would tax every request."""
    ex, gw = _extractor([_FULL])
    await ex.understand("delete the temp cache dir", _CORR)
    assert gw.calls[0].needs_deep_reasoning is False
    assert gw.calls[0].temperature == 0.0
    assert gw.calls[0].correlation_id == _CORR


@pytest.mark.asyncio
async def test_understand_produces_success_criteria_for_verification() -> None:
    """Without criteria the verifier reports not_applicable, so this is the
    link that makes Phase 12 verification actually run."""
    ex, _ = _extractor([_FULL])
    intent = await ex.understand("delete the temp cache dir", _CORR)
    assert intent.needs_verification() is True


# ---------------------------------------------------------------------------
# Deterministic floors
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bad_json_fails_toward_caution_not_toward_speed() -> None:
    ex, _ = _extractor(["I'm sorry, I can't help with that."])
    intent = await ex.understand("do something ambiguous", _CORR)
    assert intent.risk is RiskLevel.MEDIUM, "unparsed request must not be labelled low risk"
    assert intent.complexity is Complexity.MODERATE
    assert intent.reasoning_level is ReasoningLevel.L2_MULTI_STEP
    assert intent.confidence == pytest.approx(0.2, abs=0.01)
    assert intent.objective == "do something ambiguous"


@pytest.mark.asyncio
async def test_mutation_wording_raises_risk_above_low() -> None:
    """A model that labels 'delete everything' as low risk must be overridden:
    this value reaches the SafetyEngine's stakes assessment."""
    ex, _ = _extractor(['{"objective":"clean up","domain":"filesystem","risk":"low"}'])
    intent = await ex.understand("delete the old build artifacts", _CORR)
    assert intent.risk is RiskLevel.MEDIUM


@pytest.mark.asyncio
async def test_self_question_is_self_knowledge_even_when_called_small_talk() -> None:
    ex, _ = _extractor(['{"objective":"chat","domain":"conversation","risk":"low"}'])
    intent = await ex.understand("what can you do right now?", _CORR)
    assert intent.domain is TaskDomain.SELF_KNOWLEDGE


@pytest.mark.asyncio
async def test_self_question_survives_extraction_failure() -> None:
    ex, _ = _extractor(["garbage"])
    intent = await ex.understand("inspect atlas and tell me how browser navigation works", _CORR)
    assert intent.domain is TaskDomain.SELF_KNOWLEDGE


@pytest.mark.asyncio
async def test_unknown_enum_values_fall_back_instead_of_raising() -> None:
    ex, _ = _extractor(
        [
            '{"objective":"x","domain":"telepathy","risk":"catastrophic",'
            '"urgency":"immediately","complexity":"epic","confidence":"very"}'
        ]
    )
    intent = await ex.understand("x", _CORR)
    assert intent.domain is TaskDomain.UNKNOWN
    assert intent.risk is RiskLevel.MEDIUM
    assert intent.complexity is Complexity.MODERATE
    assert intent.confidence == pytest.approx(0.5, abs=0.01)


@pytest.mark.asyncio
async def test_non_list_fields_do_not_crash_extraction() -> None:
    ex, _ = _extractor(
        [
            '{"objective":"x","domain":"coding","success_criteria":"tests pass",'
            '"constraints":null,"required_capabilities":{"a":1}}'
        ]
    )
    intent = await ex.understand("x", _CORR)
    assert intent.success_criteria == ()
    assert intent.constraints == ()
    assert intent.required_capabilities == ()


@pytest.mark.asyncio
async def test_objective_is_bounded() -> None:
    ex, _ = _extractor([f'{{"objective":"{"a" * 900}","domain":"coding"}}'])
    intent = await ex.understand("x", _CORR)
    assert len(intent.objective) == 500


# ---------------------------------------------------------------------------
# Reasoning level selection (Phase 10)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("domain", "complexity", "risk", "expected"),
    [
        (TaskDomain.RESEARCH, Complexity.SIMPLE, RiskLevel.LOW, ReasoningLevel.L4_RESEARCH),
        (TaskDomain.CODING, Complexity.COMPLEX, RiskLevel.LOW, ReasoningLevel.L3_DEEP),
        (TaskDomain.FILESYSTEM, Complexity.SIMPLE, RiskLevel.HIGH, ReasoningLevel.L3_DEEP),
        (TaskDomain.CODING, Complexity.MODERATE, RiskLevel.LOW, ReasoningLevel.L2_MULTI_STEP),
        (TaskDomain.CONVERSATION, Complexity.TRIVIAL, RiskLevel.LOW, ReasoningLevel.L0_DIRECT),
        (TaskDomain.FILESYSTEM, Complexity.SIMPLE, RiskLevel.LOW, ReasoningLevel.L1_SIMPLE),
    ],
)
def test_select_reasoning_level(
    domain: TaskDomain, complexity: Complexity, risk: RiskLevel, expected: ReasoningLevel
) -> None:
    assert select_reasoning_level(domain, complexity, risk) is expected


def test_reasoning_levels_are_ordered() -> None:
    """The level indexes step and token budgets, so ordering must hold."""
    assert ReasoningLevel.L0_DIRECT < ReasoningLevel.L2_MULTI_STEP < ReasoningLevel.L4_RESEARCH


# ---------------------------------------------------------------------------
# Capabilities projection (replaces the old Router model call)
# ---------------------------------------------------------------------------


def test_capabilities_projection_needs_no_model_call() -> None:
    caps = capabilities_from_intent(TaskIntent(objective="x", domain=TaskDomain.FILESYSTEM, risk=RiskLevel.LOW))
    assert caps.needs_tools is True
    assert caps.needs_retrieval is True
    assert caps.max_risk is RiskLevel.LOW
    assert caps.needs_confirmation is False


def test_capabilities_projection_requires_confirmation_for_side_effects() -> None:
    caps = capabilities_from_intent(
        TaskIntent(
            objective="x",
            domain=TaskDomain.FILESYSTEM,
            risk=RiskLevel.LOW,
            likely_side_effects=("deletes files",),
        )
    )
    assert caps.needs_confirmation is True


def test_capabilities_projection_pure_conversation_needs_no_tools() -> None:
    caps = capabilities_from_intent(TaskIntent(objective="say hi", domain=TaskDomain.CONVERSATION))
    assert caps.needs_tools is False
    assert caps.needs_retrieval is False


def test_capabilities_projection_never_forces_cloud() -> None:
    """Egress is the model router's decision under the active cost/privacy
    policy, not the understanding stage's."""
    caps = capabilities_from_intent(TaskIntent(objective="x", domain=TaskDomain.RESEARCH, risk=RiskLevel.HIGH))
    assert caps.needs_cloud is False
