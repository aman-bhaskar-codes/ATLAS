"""Agent system — Vamos multi-agent architecture.

Exports the core abstractions for the specialist agent framework:
BaseAgent, AgentConfig, AgentRegistry, TaskDAG.
"""

from atlas.agents.base import AgentConfig, BaseAgent
from atlas.agents.registry import AgentRegistry
from atlas.agents.dag import TaskDAG, SubTask

__all__ = ["AgentConfig", "BaseAgent", "AgentRegistry", "TaskDAG", "SubTask"]
