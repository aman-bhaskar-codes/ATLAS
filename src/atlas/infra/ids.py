"""Identifier generation.

WHY typed NewTypes: TaskId/CorrelationId/ExecutionId are all strings at
runtime, but mypy treats them as distinct, so you cannot accidentally swap
them. WHY a protocol: tests inject a deterministic generator.
"""

from __future__ import annotations

import uuid
from typing import NewType, Protocol

TaskId = NewType("TaskId", str)
CorrelationId = NewType("CorrelationId", str)
ExecutionId = NewType("ExecutionId", str)


class IdGenerator(Protocol):
    def task_id(self) -> TaskId: ...
    def correlation_id(self) -> CorrelationId: ...
    def execution_id(self) -> ExecutionId: ...


class UuidGenerator:
    """Production generator. uuid4 hex is unordered but unique; ordering is
    provided by the audit timestamp column, so we do not need UUIDv7 here."""

    def task_id(self) -> TaskId:
        return TaskId(uuid.uuid4().hex)

    def correlation_id(self) -> CorrelationId:
        return CorrelationId(uuid.uuid4().hex)

    def execution_id(self) -> ExecutionId:
        return ExecutionId(uuid.uuid4().hex)
