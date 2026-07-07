import pytest

from atlas.orchestration.errors import IllegalTransitionError
from atlas.orchestration.state import TaskState, TaskStateMachine


def test_legal_path() -> None:
    m = TaskStateMachine()
    for s in (TaskState.READY, TaskState.BUILDING_CONTEXT, TaskState.PLANNING,
              TaskState.REASONING, TaskState.VALIDATING, TaskState.COMPLETED,
              TaskState.ARCHIVED):
        m.transition(s)
    assert m.is_terminal()


def test_illegal_transition_fails_loudly() -> None:
    m = TaskStateMachine()
    with pytest.raises(IllegalTransitionError):
        m.transition(TaskState.EXECUTING)  # CREATED -> EXECUTING is illegal


def test_terminal_has_no_exits_except_archive() -> None:
    m = TaskStateMachine()
    m.transition(TaskState.READY)
    m.transition(TaskState.CANCELLING)
    m.transition(TaskState.FAILED)
    assert m.can(TaskState.ARCHIVED) and not m.can(TaskState.REASONING)
