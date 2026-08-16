"""Built-in specialist agents — concrete implementations.

WHY: the Vamos architecture specifies specialist roles: researcher, writer,
coder, analyst, general. Each specialist has its own system prompt, tool
permissions, and model preferences. They all share the same ReasoningLoop
mechanics but differ in what tools they can access and how they're prompted.
"""

from __future__ import annotations

from typing import Any

from atlas.agents.base import AgentConfig, BaseAgent, SubTaskResult
from atlas.infra.ids import CorrelationId
from atlas.infra.logging import get_logger
from atlas.infra.types import ModelCapability, ModelRequest
from atlas.intelligence.gateway import ModelGateway

_log = get_logger("atlas.agents.specialists")


class SimpleSpecialist(BaseAgent):
    """Lightweight specialist: uses a single LLM call with specialist system prompt.

    For Phase 4 MVP, specialists use targeted prompting rather than full
    sub-loops. This keeps cost low while still providing role-specific behavior.
    The Supervisor's DAG decomposition does the heavy lifting — each specialist
    just needs to handle one well-scoped subtask.
    """

    def __init__(self, config: AgentConfig, *, gateway: ModelGateway) -> None:
        super().__init__(config)
        self._gw = gateway

    async def execute(
        self,
        *,
        subtask_id: str,
        description: str,
        context: dict[str, Any],
        dependency_results: dict[str, Any],
        correlation_id: CorrelationId,
    ) -> SubTaskResult:
        try:
            # Build prompt with dependency context
            parts = [f"TASK: {description}"]
            if dependency_results:
                dep_context = "\n".join(f"- {k}: {str(v)[:1000]}" for k, v in dependency_results.items())
                parts.append(f"\nPRIOR RESULTS:\n{dep_context}")
            if context:
                parts.append(f"\nCONTEXT: {context}")

            prompt = "\n".join(parts)
            resp = await self._gw.complete(
                ModelRequest(
                    correlation_id=correlation_id,
                    system=self._config.system_prompt,
                    prompt=prompt,
                    required_capabilities=frozenset(
                        {ModelCapability.CODING if self.agent_type == "coder" else ModelCapability.REASONING}
                    ),
                    max_tokens=self._config.context_window,
                    temperature=self._config.temperature,
                )
            )

            return SubTaskResult(
                subtask_id=subtask_id,
                agent_type=self.agent_type,
                ok=True,
                output=resp.text,
                cost_usd=resp.cost.usd,
            )
        except Exception as exc:
            _log.error(
                "specialist.failed",
                event_type="agents",
                agent_type=self.agent_type,
                subtask_id=subtask_id,
                error=repr(exc),
            )
            return SubTaskResult(
                subtask_id=subtask_id,
                agent_type=self.agent_type,
                ok=False,
                error=str(exc),
            )


# ── Pre-configured specialist configs ────────────────────────────────── #

RESEARCHER_CONFIG = AgentConfig(
    agent_type="researcher",
    system_prompt=(
        "You are a research specialist. Your job is to find, analyze, and "
        "synthesize information. Focus on accuracy and cite sources when possible. "
        "Be thorough but concise."
    ),
    allowed_tools=("browser", "knowledge", "memory", "http"),
    max_steps=15,
    temperature=0.1,
    context_window=4096,
)

WRITER_CONFIG = AgentConfig(
    agent_type="writer",
    system_prompt=(
        "You are a writing specialist. Your job is to compose, edit, and refine "
        "text content. Match the requested tone and format precisely. Produce "
        "publication-ready output."
    ),
    allowed_tools=("filesystem", "knowledge", "memory"),
    max_steps=10,
    temperature=0.4,
    context_window=4096,
)

CODER_CONFIG = AgentConfig(
    agent_type="coder",
    system_prompt=(
        "You are a coding specialist. Write clean, tested, production-quality code. "
        "Follow existing patterns in the codebase. Include error handling. "
        "Explain non-obvious design decisions in brief comments."
    ),
    allowed_tools=("filesystem", "shell", "code", "knowledge"),
    max_steps=20,
    temperature=0.1,
    context_window=4096,
)

ANALYST_CONFIG = AgentConfig(
    agent_type="analyst",
    system_prompt=(
        "You are a data analyst specialist. Analyze data, identify patterns, and "
        "produce clear summaries with actionable insights. Use structured output "
        "(tables, lists) where appropriate."
    ),
    allowed_tools=("filesystem", "shell", "code", "knowledge"),
    max_steps=15,
    temperature=0.1,
    context_window=4096,
)

GENERAL_CONFIG = AgentConfig(
    agent_type="general",
    system_prompt=(
        "You are a capable general-purpose assistant. Handle tasks that don't "
        "require a specialist. Be thorough and practical."
    ),
    allowed_tools=("filesystem", "shell", "browser", "knowledge", "memory"),
    max_steps=20,
    temperature=0.2,
    context_window=4096,
)

DEFAULT_SPECIALIST_CONFIGS = (
    RESEARCHER_CONFIG,
    WRITER_CONFIG,
    CODER_CONFIG,
    ANALYST_CONFIG,
    GENERAL_CONFIG,
)
