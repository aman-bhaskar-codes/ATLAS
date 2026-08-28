"""Decomposer — degrade-never-crash behaviour on hostile model output."""

from __future__ import annotations

import json

from atlas.infra.cognition import Complexity, RiskLevel
from atlas.infra.ids import CorrelationId
from atlas.orchestration.agents.decomposer import TaskDecomposer
from atlas.orchestration.agents.types import AgentRole
from tests.orchestration.agents._fakes import ScriptedGateway, intent

CORR = CorrelationId("c1")


def good_payload(**overrides: object) -> str:
    payload: dict[str, object] = {
        "delegate": True,
        "reason": "two independent branches",
        "subtasks": [
            {"id": "a", "role": "researcher", "objective": "gather sources", "max_steps": 4},
            {"id": "b", "role": "writer", "objective": "write it up", "depends_on": ["a"]},
        ],
    }
    payload.update(overrides)
    return json.dumps(payload)


async def decompose(gateway: ScriptedGateway, **kwargs: object) -> object:
    dec = TaskDecomposer(gateway, **kwargs)  # type: ignore[arg-type]
    return await dec.decompose("request", "context", intent(), CORR)


async def test_happy_path_builds_a_dag() -> None:
    outcome = await decompose(ScriptedGateway(good_payload()))

    assert outcome.should_delegate is True  # type: ignore[attr-defined]
    dag = outcome.dag  # type: ignore[attr-defined]
    assert [s.id for s in dag.subtasks] == ["st1", "st2"]
    assert dag.subtasks[0].role is AgentRole.RESEARCHER
    assert dag.subtasks[1].depends_on == ("st1",)  # model ids remapped
    assert [sorted(s.id for s in b) for b in dag.batches()] == [["st1"], ["st2"]]


async def test_trivial_complexity_skips_the_model_call_entirely() -> None:
    gateway = ScriptedGateway(good_payload())
    dec = TaskDecomposer(gateway)  # type: ignore[arg-type]

    outcome = await dec.decompose("r", "c", intent(complexity=Complexity.TRIVIAL), CORR)

    assert outcome.should_delegate is False
    assert gateway.requests == []


async def test_model_declining_is_respected() -> None:
    outcome = await decompose(ScriptedGateway(json.dumps({"delegate": False, "reason": "too simple"})))

    assert outcome.should_delegate is False  # type: ignore[attr-defined]
    assert outcome.reason == "too simple"  # type: ignore[attr-defined]


async def test_model_exception_degrades_to_no_delegation() -> None:
    outcome = await decompose(ScriptedGateway(RuntimeError("provider down")))

    assert outcome.should_delegate is False  # type: ignore[attr-defined]
    assert "provider down" in outcome.reason  # type: ignore[attr-defined]


async def test_unparseable_output_degrades_to_no_delegation() -> None:
    outcome = await decompose(ScriptedGateway("I am afraid I cannot do that."))

    assert outcome.should_delegate is False  # type: ignore[attr-defined]


async def test_json_array_instead_of_object_degrades() -> None:
    outcome = await decompose(ScriptedGateway('[{"delegate": true}]'))

    assert outcome.should_delegate is False  # type: ignore[attr-defined]


async def test_single_usable_subtask_is_not_worth_delegating() -> None:
    payload = good_payload(subtasks=[{"id": "a", "objective": "only one"}])

    outcome = await decompose(ScriptedGateway(payload))

    assert outcome.should_delegate is False  # type: ignore[attr-defined]
    assert "serial" in outcome.reason  # type: ignore[attr-defined]


async def test_entries_without_an_objective_are_skipped() -> None:
    payload = good_payload(
        subtasks=[
            {"id": "a", "objective": "real work"},
            {"id": "b", "objective": "   "},
            {"id": "c", "objective": "more real work"},
        ]
    )

    outcome = await decompose(ScriptedGateway(payload))

    dag = outcome.dag  # type: ignore[attr-defined]
    assert [s.objective for s in dag.subtasks] == ["real work", "more real work"]


async def test_subtask_count_is_clamped_to_the_configured_ceiling() -> None:
    payload = good_payload(subtasks=[{"id": f"x{i}", "objective": f"work {i}"} for i in range(20)])

    outcome = await decompose(ScriptedGateway(payload), max_subtasks=3)

    assert len(outcome.dag.subtasks) == 3  # type: ignore[attr-defined]


async def test_step_budget_is_clamped_and_survives_garbage() -> None:
    payload = good_payload(
        subtasks=[
            {"id": "a", "objective": "x", "max_steps": 9999},
            {"id": "b", "objective": "y", "max_steps": "not a number"},
            {"id": "c", "objective": "z", "max_steps": -5},
        ]
    )

    outcome = await decompose(ScriptedGateway(payload), max_subtasks=6, max_steps_per_subtask=5)

    assert [s.max_steps for s in outcome.dag.subtasks] == [5, 5, 1]  # type: ignore[attr-defined]


async def test_duplicate_model_ids_do_not_produce_dangling_edges() -> None:
    payload = good_payload(
        subtasks=[
            {"id": "dup", "objective": "first"},
            {"id": "dup", "objective": "second"},
            {"id": "c", "objective": "third", "depends_on": ["dup"]},
        ]
    )

    outcome = await decompose(ScriptedGateway(payload))

    dag = outcome.dag  # type: ignore[attr-defined]
    known = {s.id for s in dag.subtasks}
    for s in dag.subtasks:
        assert set(s.depends_on) <= known


async def test_non_list_depends_on_is_ignored() -> None:
    payload = good_payload(
        subtasks=[
            {"id": "a", "objective": "x"},
            {"id": "b", "objective": "y", "depends_on": "a"},
        ]
    )

    outcome = await decompose(ScriptedGateway(payload))

    assert outcome.dag.subtasks[1].depends_on == ()  # type: ignore[attr-defined]


async def test_subtasks_not_a_list_degrades() -> None:
    outcome = await decompose(ScriptedGateway(json.dumps({"delegate": True, "subtasks": "nope"})))

    assert outcome.should_delegate is False  # type: ignore[attr-defined]


async def test_json_embedded_in_prose_is_recovered() -> None:
    wrapped = f"Sure! Here is the plan:\n```json\n{good_payload()}\n```\nHope that helps."

    outcome = await decompose(ScriptedGateway(wrapped))

    assert outcome.should_delegate is True  # type: ignore[attr-defined]


async def test_subtask_risk_inherits_the_parent_intent() -> None:
    gateway = ScriptedGateway(good_payload())
    dec = TaskDecomposer(gateway)  # type: ignore[arg-type]

    outcome = await dec.decompose(
        "r",
        "c",
        intent().model_copy(update={"risk": RiskLevel.MEDIUM}),
        CORR,
    )

    assert all(s.risk is RiskLevel.MEDIUM for s in outcome.dag.subtasks)  # type: ignore[attr-defined]


async def test_decomposition_is_a_read_only_model_call() -> None:
    """It must never be routed as a consequential action."""
    from atlas.infra.types import Tier

    gateway = ScriptedGateway(good_payload())
    await decompose(gateway)

    assert gateway.requests[0].stakes_tier is Tier.AUTO
