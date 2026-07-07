"""Deterministic task state machine.

WHY an explicit transition table: the lifecycle of every task must be legible
and tamper-evident. Any transition not in _LEGAL raises IllegalTransitionError
immediately — no silent 'impossible' states. The machine holds NO business
logic; it only guards transitions. The orchestrator drives it.
"""

from __future__ import annotations

from enum import StrEnum

from atlas.orchestration.errors import IllegalTransitionError


class TaskState(StrEnum):
    CREATED = "created"
    READY = "ready"
    BUILDING_CONTEXT = "building_context"
    PLANNING = "planning"
    REASONING = "reasoning"
    WAITING_TOOL = "waiting_tool"
    WAITING_CONFIRMATION = "waiting_confirmation"
    EXECUTING = "executing"
    OBSERVING = "observing"
    VALIDATING = "validating"
    RETRYING = "retrying"
    CANCELLING = "cancelling"
    FAILED = "failed"
    COMPLETED = "completed"
    ARCHIVED = "archived"


_TERMINAL = frozenset({TaskState.FAILED, TaskState.COMPLETED, TaskState.ARCHIVED})

# Legal transitions. Anything not listed is illegal and fails loudly.
_LEGAL: dict[TaskState, frozenset[TaskState]] = {
    TaskState.CREATED: frozenset({TaskState.READY, TaskState.CANCELLING}),
    TaskState.READY: frozenset({TaskState.BUILDING_CONTEXT, TaskState.CANCELLING}),
    TaskState.BUILDING_CONTEXT: frozenset({
        TaskState.PLANNING, TaskState.FAILED, TaskState.CANCELLING,
    }),
    TaskState.PLANNING: frozenset({
        TaskState.REASONING, TaskState.FAILED, TaskState.CANCELLING,
    }),
    TaskState.REASONING: frozenset({
        TaskState.WAITING_TOOL, TaskState.WAITING_CONFIRMATION,
        TaskState.VALIDATING, TaskState.FAILED, TaskState.CANCELLING,
    }),
    TaskState.WAITING_CONFIRMATION: frozenset({
        TaskState.EXECUTING, TaskState.REASONING, TaskState.FAILED, TaskState.CANCELLING,
    }),
    TaskState.WAITING_TOOL: frozenset({
        TaskState.EXECUTING, TaskState.FAILED, TaskState.CANCELLING,
    }),
    TaskState.EXECUTING: frozenset({
        TaskState.OBSERVING, TaskState.FAILED, TaskState.CANCELLING,
    }),
    TaskState.OBSERVING: frozenset({
        TaskState.REASONING, TaskState.VALIDATING, TaskState.RETRYING,
        TaskState.FAILED, TaskState.CANCELLING,
    }),
    TaskState.VALIDATING: frozenset({
        TaskState.COMPLETED, TaskState.REASONING, TaskState.RETRYING,
        TaskState.FAILED, TaskState.CANCELLING,
    }),
    TaskState.RETRYING: frozenset({TaskState.REASONING, TaskState.FAILED, TaskState.CANCELLING}),
    TaskState.CANCELLING: frozenset({TaskState.FAILED}),
    TaskState.COMPLETED: frozenset({TaskState.ARCHIVED}),
    TaskState.FAILED: frozenset({TaskState.ARCHIVED}),
    TaskState.ARCHIVED: frozenset(),
}


class TaskStateMachine:
    def __init__(self, initial: TaskState = TaskState.CREATED) -> None:
        self._state = initial
        self._history: list[TaskState] = [initial]

    @property
    def state(self) -> TaskState:
        return self._state

    @property
    def history(self) -> tuple[TaskState, ...]:
        return tuple(self._history)

    def is_terminal(self) -> bool:
        return self._state in _TERMINAL

    def can(self, target: TaskState) -> bool:
        return target in _LEGAL.get(self._state, frozenset())

    def transition(self, target: TaskState) -> TaskState:
        if not self.can(target):
            raise IllegalTransitionError(
                f"illegal transition {self._state.value} -> {target.value}"
            )
        self._state = target
        self._history.append(target)
        return target
