"""Agent system — Vamos multi-agent architecture.

Exports the core abstractions for the specialist agent framework:
BaseAgent, AgentConfig, AgentRegistry, TaskDAG.
"""

from atlas.agents.base import AgentConfig, BaseAgent
from atlas.agents.dag import SubTask, TaskDAG
from atlas.agents.registry import AgentRegistry

__all__ = ["AgentConfig", "AgentRegistry", "BaseAgent", "SubTask", "TaskDAG"]
