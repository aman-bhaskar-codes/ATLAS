"""Base agent abstractions — the Vamos agent protocol.

WHY: every specialist agent (researcher, writer, coder) shares a common
contract. AgentConfig declares what tools and models an agent uses.
BaseAgent defines the execution protocol. The Supervisor decomposes tasks
into a DAG of SubTasks, each assigned to a specialist.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from atlas.infra.ids import CorrelationId


@dataclass(frozen=True)
class AgentConfig:
    """Static configuration for a specialist agent type."""

    agent_type: str  # "researcher", "writer", "coder", etc.
    system_prompt: str  # Loaded from prompts/system/
    allowed_tools: tuple[str, ...] = ()  # Subset of all tools this agent can use
    preferred_model: str = "auto"  # "auto" lets the router decide
    fallback_models: tuple[str, ...] = ()  # Ordered fallback chain
    max_steps: int = 20  # Reasoning loop step limit
    temperature: float = 0.2  # LLM temperature
    context_window: int = 4096  # Max tokens to feed


@dataclass
class SubTaskInput:
    """Data flowing into a subtask from its dependencies."""

    description: str
    context: dict[str, Any] = field(default_factory=dict)
    dependency_results: dict[str, Any] = field(default_factory=dict)


@dataclass
class SubTaskResult:
    """Outcome of a specialist agent executing a subtask."""

    subtask_id: str
    agent_type: str
    ok: bool
    output: Any = None
    error: str | None = None
    steps_taken: int = 0
    cost_usd: float = 0.0


class BaseAgent(ABC):
    """Protocol that every specialist agent implements.

    The Supervisor dispatches SubTasks to specialist agents via execute().
    Each agent runs its own bounded reasoning loop with its allowed tools.
    """

    def __init__(self, config: AgentConfig) -> None:
        self._config = config

    @property
    def config(self) -> AgentConfig:
        return self._config

    @property
    def agent_type(self) -> str:
        return self._config.agent_type

    @abstractmethod
    async def execute(
        self,
        *,
        subtask_id: str,
        description: str,
        context: dict[str, Any],
        dependency_results: dict[str, Any],
        correlation_id: CorrelationId,
    ) -> SubTaskResult:
        """Execute a subtask. Returns structured result."""
        ...
