"""Shared fakes for the multi-agent layer tests."""

from __future__ import annotations

from typing import Any

from atlas.infra.cognition import Complexity, TaskDomain, TaskIntent
from atlas.infra.types import ModelRequest, ModelResponse, ModelTarget
from atlas.orchestration.agents.types import SubTask, SubTaskResult, SubTaskStatus


class ScriptedGateway:
    """Returns queued responses in order; raises a queued exception if given one."""

    def __init__(self, *responses: str | Exception) -> None:
        self._queue: list[str | Exception] = list(responses)
        self.requests: list[ModelRequest] = []

    async def complete(self, req: ModelRequest) -> ModelResponse:
        self.requests.append(req)
        item = self._queue.pop(0) if self._queue else ""
        if isinstance(item, Exception):
            raise item
        return ModelResponse(text=item, target=ModelTarget.LOCAL_FAST, model="fake")


class RecordingEvents:
    """Captures EventPublisher.emit calls without a bus."""

    def __init__(self) -> None:
        self.emitted: list[dict[str, Any]] = []

    async def emit(self, **kwargs: Any) -> None:
        self.emitted.append(kwargs)

    def kinds(self) -> list[str]:
        return [e["kind"] for e in self.emitted]


class ScriptedSpecialist:
    """Maps subtask id -> outcome. Records execution order and upstream input."""

    def __init__(
        self,
        outcomes: dict[str, SubTaskStatus] | None = None,
        *,
        raises: dict[str, Exception] | None = None,
    ) -> None:
        self._outcomes = outcomes or {}
        self._raises = raises or {}
        self.ran: list[str] = []
        self.upstream_seen: dict[str, str] = {}

    async def run(
        self,
        *,
        subtask: SubTask,
        parent_task_id: str,
        correlation_id: str,
        base_context: str,
        upstream: str,
        token: Any,
    ) -> SubTaskResult:
        self.ran.append(subtask.id)
        self.upstream_seen[subtask.id] = upstream
        if exc := self._raises.get(subtask.id):
            raise exc
        status = self._outcomes.get(subtask.id, SubTaskStatus.SUCCEEDED)
        return SubTaskResult(
            subtask_id=subtask.id,
            role=subtask.role,
            status=status,
            output=f"output of {subtask.id}" if status is SubTaskStatus.SUCCEEDED else "",
            error=None if status is SubTaskStatus.SUCCEEDED else "boom",
            steps_taken=2,
            tool_calls=1,
            model_calls=1,
            tokens_used=100,
        )


def intent(
    *,
    complexity: Complexity = Complexity.COMPLEX,
    objective: str = "do the thing",
    domain: TaskDomain = TaskDomain.RESEARCH,
) -> TaskIntent:
    return TaskIntent(objective=objective, domain=domain, complexity=complexity)
