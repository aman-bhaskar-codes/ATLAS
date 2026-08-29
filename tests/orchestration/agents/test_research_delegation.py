"""Research-aware multi-agent decomposition (R7).

Multi-agent research is not a second engine — it is the generic delegation layer
pointed at the knowledge fabric. These tests pin the research-specific seam the
decomposer adds: research goals split into parallel researcher branches, and a
researcher branch always reaches for the evidence tool and cites, so it gathers
evidence through the R2-R6 pipeline instead of guessing. Everything below the
decomposition (safety funnel, verifier, conflict scan) is unchanged and covered
by the sibling suites, so it is not re-tested here.
"""

from __future__ import annotations

import json

from atlas.infra.cognition import Complexity, TaskDomain
from atlas.infra.ids import CorrelationId
from atlas.infra.types import Tier
from atlas.orchestration.agents.decomposer import TaskDecomposer
from atlas.orchestration.agents.types import AgentRole
from tests.orchestration.agents._fakes import ScriptedGateway, intent

CORR = CorrelationId("c1")


def _payload(subtasks: list[dict[str, object]], *, reason: str = "independent facets") -> str:
    return json.dumps({"delegate": True, "reason": reason, "subtasks": subtasks})


async def _decompose(gateway: ScriptedGateway, *, domain: TaskDomain) -> object:
    dec = TaskDecomposer(gateway)  # type: ignore[arg-type]
    return await dec.decompose("investigate X", "context", intent(domain=domain), CORR)


async def test_research_domain_biases_the_prompt_toward_parallel_sub_questions() -> None:
    gateway = ScriptedGateway(_payload([{"id": "a", "role": "researcher", "objective": "facet one"}]))

    await _decompose(gateway, domain=TaskDomain.RESEARCH)

    system = gateway.requests[0].system
    assert "RESEARCH request" in system
    assert "INDEPENDENT sub-questions" in system
    # Still a read-only planning call: decomposition itself touches nothing.
    assert gateway.requests[0].stakes_tier is Tier.AUTO


async def test_non_research_domain_keeps_the_generic_prompt() -> None:
    gateway = ScriptedGateway(_payload([{"id": "a", "role": "coder", "objective": "fix bug"}]))

    await _decompose(gateway, domain=TaskDomain.CODING)

    assert "RESEARCH request" not in gateway.requests[0].system


async def test_researcher_branches_default_to_the_knowledge_tool_and_a_cite_criterion() -> None:
    gateway = ScriptedGateway(
        _payload(
            [
                {"id": "a", "role": "researcher", "objective": "what does source A say"},
                {"id": "b", "role": "researcher", "objective": "what does source B say"},
            ]
        )
    )

    outcome = await _decompose(gateway, domain=TaskDomain.RESEARCH)

    dag = outcome.dag  # type: ignore[attr-defined]
    assert [s.role for s in dag.subtasks] == [AgentRole.RESEARCHER, AgentRole.RESEARCHER]
    for s in dag.subtasks:
        assert "knowledge" in s.suggested_tools  # reaches the R2-R6 evidence pipeline
        assert s.success_criteria == ("cite a source for each claim",)


async def test_model_supplied_tools_and_criteria_win_over_the_defaults() -> None:
    gateway = ScriptedGateway(
        _payload(
            [
                {
                    "id": "a",
                    "role": "researcher",
                    "objective": "gather",
                    "suggested_tools": ["filesystem"],
                    "success_criteria": ["find the config path"],
                },
                {"id": "b", "role": "writer", "objective": "write it up", "depends_on": ["a"]},
            ]
        )
    )

    outcome = await _decompose(gateway, domain=TaskDomain.RESEARCH)

    researcher = outcome.dag.subtasks[0]  # type: ignore[attr-defined]
    assert researcher.suggested_tools == ("filesystem",)  # not overwritten
    assert researcher.success_criteria == ("find the config path",)


async def test_defaults_apply_only_to_researcher_roles() -> None:
    gateway = ScriptedGateway(
        _payload(
            [
                {"id": "a", "role": "researcher", "objective": "gather"},
                {"id": "b", "role": "analyst", "objective": "compute", "depends_on": ["a"]},
            ]
        )
    )

    outcome = await _decompose(gateway, domain=TaskDomain.RESEARCH)

    researcher, analyst = outcome.dag.subtasks  # type: ignore[attr-defined]
    assert "knowledge" in researcher.suggested_tools
    assert analyst.suggested_tools == ()  # analyst reasons over supplied data, not the index
    assert analyst.success_criteria == ()


async def test_trivial_research_still_skips_delegation() -> None:
    """The research nudge must not lower the complexity gate — trivial stays serial."""
    gateway = ScriptedGateway(_payload([{"id": "a", "role": "researcher", "objective": "x"}]))
    dec = TaskDecomposer(gateway)  # type: ignore[arg-type]

    outcome = await dec.decompose("q", "c", intent(domain=TaskDomain.RESEARCH, complexity=Complexity.TRIVIAL), CORR)

    assert outcome.should_delegate is False  # type: ignore[attr-defined]
    assert gateway.requests == []  # no model call at all
