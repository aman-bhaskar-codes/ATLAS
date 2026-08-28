"""Synthesizer — never empty, never raising, never inventing.

Synthesis is the last step of a delegated run, so a failure here would throw
away work that already succeeded. Every path must still hand back something
usable.
"""

from __future__ import annotations

from atlas.infra.ids import CorrelationId
from atlas.infra.types import Tier
from atlas.orchestration.agents.synthesizer import Synthesizer
from atlas.orchestration.agents.types import AgentRole, SubTaskResult, SubTaskStatus
from tests.orchestration.agents._fakes import ScriptedGateway

CORR = CorrelationId("c1")


def res(
    sid: str,
    status: SubTaskStatus = SubTaskStatus.SUCCEEDED,
    *,
    output: str = "",
    error: str | None = None,
    role: AgentRole = AgentRole.GENERAL,
) -> SubTaskResult:
    return SubTaskResult(subtask_id=sid, role=role, status=status, output=output, error=error)


async def synth(gateway: ScriptedGateway, *results: SubTaskResult, **kwargs: object) -> str:
    s = Synthesizer(gateway, **kwargs)  # type: ignore[arg-type]
    return await s.synthesize(request="the original request", results=results, correlation_id=CORR)


async def test_no_results_returns_a_plain_statement() -> None:
    gateway = ScriptedGateway("unused")

    answer = await synth(gateway)

    assert answer == "No subtask produced a result."
    assert gateway.requests == []


async def test_the_model_answer_is_used_when_there_is_material() -> None:
    gateway = ScriptedGateway("  the merged answer  ")

    answer = await synth(gateway, res("st1", output="finding one"), res("st2", output="finding two"))

    assert answer == "the merged answer"
    prompt = gateway.requests[0].prompt
    assert "the original request" in prompt
    assert "finding one" in prompt and "finding two" in prompt


async def test_a_model_failure_degrades_to_the_deterministic_digest() -> None:
    answer = await synth(ScriptedGateway(RuntimeError("provider down")), res("st1", output="finding one"))

    assert "finding one" in answer
    assert "[st1 · general · succeeded]" in answer


async def test_an_empty_model_response_degrades_to_the_digest() -> None:
    answer = await synth(ScriptedGateway("   "), res("st1", output="finding one"))

    assert "finding one" in answer


async def test_all_subtasks_failing_skips_the_model_call_entirely() -> None:
    """Paraphrasing failures back at the user is not worth a round trip."""
    gateway = ScriptedGateway("should not be used")

    answer = await synth(
        gateway,
        res("st1", SubTaskStatus.FAILED, error="tool exploded"),
        res("st2", SubTaskStatus.SKIPPED),
    )

    assert gateway.requests == []
    assert "FAILED: tool exploded" in answer
    assert "Not attempted" in answer


async def test_a_succeeded_but_empty_output_is_not_treated_as_material() -> None:
    gateway = ScriptedGateway("should not be used")

    answer = await synth(gateway, res("st1", output=""))

    assert gateway.requests == []
    assert "(no output)" in answer


async def test_the_digest_labels_every_subtask_exactly_once() -> None:
    answer = await synth(
        ScriptedGateway(RuntimeError("down")),
        res("st1", output="a", role=AgentRole.RESEARCHER),
        res("st2", SubTaskStatus.FAILED, error="boom", role=AgentRole.CODER),
        res("st3", SubTaskStatus.SKIPPED, role=AgentRole.WRITER),
    )

    assert answer.count("[st1 ·") == 1
    assert "[st2 · coder · failed]" in answer
    assert "[st3 · writer · skipped]" in answer


async def test_long_specialist_output_is_truncated_before_prompting() -> None:
    gateway = ScriptedGateway("ok")

    await synth(gateway, res("st1", output="x" * 20_000), res("st2", output="y" * 20_000))

    # 6000 chars per specialist, so the prompt cannot grow without bound.
    assert gateway.requests[0].prompt.count("x") == 6000
    assert gateway.requests[0].prompt.count("y") == 6000


async def test_synthesis_is_a_read_only_model_call() -> None:
    gateway = ScriptedGateway("ok")

    await synth(gateway, res("st1", output="a"))

    assert gateway.requests[0].stakes_tier is Tier.AUTO


async def test_the_synthesis_token_budget_is_configurable() -> None:
    gateway = ScriptedGateway("ok")

    await synth(gateway, res("st1", output="a"), max_tokens=999)

    assert gateway.requests[0].max_tokens == 999
