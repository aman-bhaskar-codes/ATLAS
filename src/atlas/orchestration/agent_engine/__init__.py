"""Agent Execution Engine (M0.2) + run context substrate (M0.5).

A governed, provider-native tool-calling loop that reuses ATLAS's live funnel
(ToolDispatcher -> SafetyEngine), seatbelts (LimitCounter), and event bus (see
``engine.AgentEngine``), plus the durable/resumable substrate around it: the
``AgentRunRecord`` envelope, the ``AgentRunStore`` seam (SQLite + reserved
Postgres impls), and the pure ``context`` helpers that seed and rehydrate a run's
conversation.
"""

from __future__ import annotations

from atlas.orchestration.agent_engine.context import continue_messages, reconstruct_conversation, seed_messages
from atlas.orchestration.agent_engine.engine import AgentEngine, SupportsDispatch, SupportsInfer
from atlas.orchestration.agent_engine.event_reader import (
    AgentEventReader,
    AgentRunEvent,
    SqliteAgentEventReader,
)
from atlas.orchestration.agent_engine.persistence import (
    AgentRunStore,
    PostgresAgentRunStore,
    SqliteAgentRunStore,
)
from atlas.orchestration.agent_engine.records import (
    TERMINAL_RUN_STATUSES,
    AgentEngineResult,
    AgentRunRecord,
    AgentStep,
    RunStatus,
    StopReason,
    ToolCallRecord,
    serialize_tool_payload,
)
from atlas.orchestration.agent_engine.service import (
    DEFAULT_AGENT_SYSTEM_PROMPT,
    AgentRunError,
    AgentRunNotReady,
    AgentRunService,
)

__all__ = [
    "DEFAULT_AGENT_SYSTEM_PROMPT",
    "TERMINAL_RUN_STATUSES",
    "AgentEngine",
    "AgentEngineResult",
    "AgentEventReader",
    "AgentRunError",
    "AgentRunEvent",
    "AgentRunNotReady",
    "AgentRunRecord",
    "AgentRunService",
    "AgentRunStore",
    "AgentStep",
    "PostgresAgentRunStore",
    "RunStatus",
    "SqliteAgentEventReader",
    "SqliteAgentRunStore",
    "StopReason",
    "SupportsDispatch",
    "SupportsInfer",
    "ToolCallRecord",
    "continue_messages",
    "reconstruct_conversation",
    "seed_messages",
    "serialize_tool_payload",
]
