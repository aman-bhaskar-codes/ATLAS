"""Multi-agent specialist layer.

A strategy *inside* the single task pipeline: the Orchestrator asks the
supervisor whether a request is worth splitting, and falls back to the serial
OTAR loop whenever the answer is no. Every delegated tool call travels the same
ReasoningLoop -> ToolDispatcher -> SafetyEngine funnel as a non-delegated one.
"""

from __future__ import annotations

from atlas.orchestration.agents.decomposer import TaskDecomposer
from atlas.orchestration.agents.specialists import Specialist
from atlas.orchestration.agents.supervisor import AgentSupervisor, SupervisionResult
from atlas.orchestration.agents.synthesizer import Synthesizer
from atlas.orchestration.agents.types import (
    AgentRole,
    DecompositionOutcome,
    SubTask,
    SubTaskResult,
    SubTaskStatus,
    TaskDAG,
)

__all__ = [
    "AgentRole",
    "AgentSupervisor",
    "DecompositionOutcome",
    "Specialist",
    "SubTask",
    "SubTaskResult",
    "SubTaskStatus",
    "SupervisionResult",
    "Synthesizer",
    "TaskDAG",
    "TaskDecomposer",
]
