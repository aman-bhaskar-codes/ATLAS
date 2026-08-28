"""TaskDAG invariants — the executor's correctness depends entirely on these."""

from __future__ import annotations

from atlas.orchestration.agents.types import AgentRole, SubTask, SubTaskStatus, TaskDAG


def st(sid: str, *deps: str, role: AgentRole = AgentRole.GENERAL) -> SubTask:
    return SubTask(id=sid, role=role, objective=f"objective {sid}", depends_on=deps)


def test_independent_subtasks_form_one_batch() -> None:
    dag = TaskDAG.build("goal", (st("st1"), st("st2"), st("st3")))

    batches = dag.batches()

    assert len(batches) == 1
    assert {s.id for s in batches[0]} == {"st1", "st2", "st3"}
    assert dag.repairs == ()


def test_dependencies_are_ordered_into_successive_batches() -> None:
    dag = TaskDAG.build("goal", (st("st1"), st("st2"), st("st3", "st1", "st2")))

    batches = dag.batches()

    assert [sorted(s.id for s in b) for b in batches] == [["st1", "st2"], ["st3"]]


def test_every_subtask_appears_exactly_once_across_batches() -> None:
    dag = TaskDAG.build("goal", (st("st1"), st("st2", "st1"), st("st3", "st2"), st("st4", "st1")))

    seen = [s.id for batch in dag.batches() for s in batch]

    assert sorted(seen) == ["st1", "st2", "st3", "st4"]
    assert len(seen) == len(set(seen))


def test_unknown_dependency_is_dropped_and_recorded() -> None:
    dag = TaskDAG.build("goal", (st("st1"), st("st2", "does_not_exist")))

    assert dag.subtasks[1].depends_on == ()
    assert any("unknown" in r for r in dag.repairs)
    assert len(dag.batches()) == 1


def test_self_dependency_is_dropped() -> None:
    dag = TaskDAG.build("goal", (st("st1", "st1"),))

    assert dag.subtasks[0].depends_on == ()
    assert len(dag.batches()) == 1


def test_cycle_is_broken_rather_than_raised() -> None:
    """A model can emit a cycle. Losing the whole task to it is worse than
    running the members concurrently."""
    dag = TaskDAG.build("goal", (st("st1", "st2"), st("st2", "st1")))

    batches = dag.batches()

    assert any("cycle" in r for r in dag.repairs)
    assert [s.id for s in batches[0]] != []
    assert sum(len(b) for b in batches) == 2


def test_batches_terminate_even_on_a_hand_built_impossible_graph() -> None:
    """batches() must never spin: constructed directly, bypassing build()."""
    dag = TaskDAG(goal="goal", subtasks=(st("st1", "st2"), st("st2", "st1")))

    assert dag.batches() == ()


def test_dependents_of_is_transitive() -> None:
    dag = TaskDAG.build("goal", (st("st1"), st("st2", "st1"), st("st3", "st2"), st("st4")))

    assert dag.dependents_of("st1") == frozenset({"st2", "st3"})
    assert dag.dependents_of("st4") == frozenset()


def test_dependents_of_terminates_on_a_cyclic_hand_built_graph() -> None:
    dag = TaskDAG(goal="goal", subtasks=(st("st1", "st2"), st("st2", "st1")))

    assert dag.dependents_of("st1") == frozenset({"st1", "st2"})


def test_role_parse_never_raises() -> None:
    assert AgentRole.parse("researcher") is AgentRole.RESEARCHER
    assert AgentRole.parse("RESEARCHER ") is AgentRole.RESEARCHER
    assert AgentRole.parse("nonsense") is AgentRole.GENERAL
    assert AgentRole.parse(None) is AgentRole.GENERAL
    assert AgentRole.parse(AgentRole.CODER) is AgentRole.CODER


def test_subtask_status_ok_only_for_succeeded() -> None:
    from atlas.orchestration.agents.types import SubTaskResult

    def result(status: SubTaskStatus) -> SubTaskResult:
        return SubTaskResult(subtask_id="st1", role=AgentRole.GENERAL, status=status)

    assert result(SubTaskStatus.SUCCEEDED).ok
    assert not result(SubTaskStatus.FAILED).ok
    assert not result(SubTaskStatus.SKIPPED).ok
