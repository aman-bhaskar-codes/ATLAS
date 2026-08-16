"""Agent registry — register agent types at startup, instantiate on demand.

WHY: the Supervisor needs to look up specialist agents by type string.
The registry maps type names to (config, factory) pairs. Agents are
instantiated lazily when dispatched to, not at boot time.
"""

from __future__ import annotations

from collections.abc import Callable

from atlas.agents.base import AgentConfig, BaseAgent
from atlas.infra.logging import get_logger

_log = get_logger("atlas.agents.registry")

AgentFactory = Callable[[AgentConfig], BaseAgent]


class AgentRegistry:
    """Register agent types at startup. Instantiate on demand."""

    def __init__(self) -> None:
        self._configs: dict[str, AgentConfig] = {}
        self._factories: dict[str, AgentFactory] = {}

    def register(self, config: AgentConfig, factory: AgentFactory) -> None:
        """Register a specialist agent type with its config and factory."""
        if config.agent_type in self._configs:
            _log.warning("agents.duplicate_register", event_type="agents", agent_type=config.agent_type)
        self._configs[config.agent_type] = config
        self._factories[config.agent_type] = factory
        _log.info(
            "agents.registered", event_type="agents", agent_type=config.agent_type, tools=list(config.allowed_tools)
        )

    def get(self, agent_type: str) -> BaseAgent:
        """Instantiate an agent by type. Raises KeyError if not registered."""
        if agent_type not in self._configs:
            raise KeyError(f"Agent type {agent_type!r} not registered. Available: {list(self._configs.keys())}")
        config = self._configs[agent_type]
        factory = self._factories[agent_type]
        return factory(config)

    def has(self, agent_type: str) -> bool:
        return agent_type in self._configs

    def list_agents(self) -> list[AgentConfig]:
        """Return configs for all registered agent types."""
        return list(self._configs.values())

    @property
    def agent_types(self) -> list[str]:
        return list(self._configs.keys())
