"""Supervisor agent — decomposes complex tasks into specialist DAGs.

WHY: the Vamos architecture specifies that complex tasks get decomposed by
the Supervisor into a DAG of subtasks, each assigned to a specialist agent.
Simple tasks bypass the supervisor and go directly to a single agent.

The Supervisor:
1. Analyzes the task description
2. Produces a TaskDAG via LLM structured output
3. Executes subtasks in topological order (parallel where possible)
4. Synthesizes final results from all subtask outputs
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from atlas.agents.base import AgentConfig, BaseAgent, SubTaskResult
from atlas.agents.dag import SubTask, TaskDAG
from atlas.agents.registry import AgentRegistry
from atlas.infra.ids import CorrelationId, IdGenerator
from atlas.infra.logging import get_logger
from atlas.infra.types import ModelRequest, ModelTarget
from atlas.intelligence.gateway import ModelGateway

_log = get_logger("atlas.agents.supervisor")

_DECOMPOSE_SYSTEM = (
    "You are a task decomposition engine. Given a complex task, break it into "
    "a DAG of subtasks. Each subtask must specify:\n"
    '- "id": unique string\n'
    '- "description": what to do\n'
    '- "agent_type": which specialist ("researcher", "writer", "coder", "analyst", "general")\n'
    '- "dependencies": list of subtask IDs that must complete first\n\n'
    "Output ONLY valid JSON: "
    '{"subtasks": [{"id": str, "description": str, "agent_type": str, "dependencies": [str]}]}\n'
    "Keep the DAG minimal. Prefer fewer, well-scoped subtasks over many trivial ones. "
    "Use 'general' for tasks that don't fit a specialist."
)

_SYNTHESIZE_SYSTEM = (
    "You are a synthesis engine. Given the original task and all subtask results, "
    "produce a coherent, complete final answer. Combine all outputs into a single "
    "well-structured response. Do NOT add information beyond what the subtasks produced."
)


class SupervisorAgent(BaseAgent):
    """Meta-agent that decomposes tasks and delegates to specialists."""

    def __init__(
        self, config: AgentConfig, *,
        gateway: ModelGateway, registry: AgentRegistry,
        ids: IdGenerator,
    ) -> None:
        super().__init__(config)
        self._gw = gateway
        self._registry = registry
        self._ids = ids

    async def execute(
        self, *, subtask_id: str, description: str,
        context: dict[str, Any],
        dependency_results: dict[str, Any],
        correlation_id: CorrelationId,
    ) -> SubTaskResult:
        """Decompose, orchestrate, and synthesize a complex task."""
        try:
            # 1. Decompose into DAG
            dag = await self.decompose(description, correlation_id)
            errors = dag.validate()
            if errors:
                _log.warning("supervisor.dag_invalid", event_type="agents",
                             correlation_id=correlation_id, errors=errors)
                return SubTaskResult(
                    subtask_id=subtask_id, agent_type=self.agent_type,
                    ok=False, error=f"Invalid DAG: {errors}",
                )

            # 2. Execute DAG
            results = await self.orchestrate(dag, correlation_id)

            # 3. Synthesize
            answer = await self.synthesize(description, results, correlation_id)
            total_cost = sum(r.cost_usd for r in results.values())
            total_steps = sum(r.steps_taken for r in results.values())

            return SubTaskResult(
                subtask_id=subtask_id, agent_type=self.agent_type,
                ok=True, output=answer,
                steps_taken=total_steps, cost_usd=total_cost,
            )
        except Exception as exc:
            _log.error("supervisor.failed", event_type="agents",
                       correlation_id=correlation_id, error=repr(exc))
            return SubTaskResult(
                subtask_id=subtask_id, agent_type=self.agent_type,
                ok=False, error=str(exc),
            )

    async def decompose(self, task: str, correlation_id: CorrelationId) -> TaskDAG:
        """LLM call: produce a DAG of subtasks from a task description."""
        available = ", ".join(self._registry.agent_types) or "general"
        prompt = (
            f"TASK: {task}\n\n"
            f"AVAILABLE AGENT TYPES: {available}\n\n"
            "Decompose this task into subtasks."
        )
        resp = await self._gw.complete(ModelRequest(
            correlation_id=correlation_id, system=_DECOMPOSE_SYSTEM,
            prompt=prompt, max_tokens=2048, temperature=0.1,
        ))
        return self._parse_dag(resp.text, task)

    async def orchestrate(
        self, dag: TaskDAG, correlation_id: CorrelationId,
    ) -> dict[str, SubTaskResult]:
        """Execute subtasks respecting dependency order. Parallel where possible."""
        completed: dict[str, SubTaskResult] = {}

        for batch in dag.topological_batches():
            tasks = []
            for subtask in batch:
                dep_results = {
                    dep_id: completed[dep_id].output
                    for dep_id in subtask.dependencies
                    if dep_id in completed
                }
                tasks.append(self._dispatch(subtask, dep_results, correlation_id))

            results = await asyncio.gather(*tasks, return_exceptions=True)
            for subtask, result in zip(batch, results):
                if isinstance(result, Exception):
                    completed[subtask.id] = SubTaskResult(
                        subtask_id=subtask.id, agent_type=subtask.agent_type,
                        ok=False, error=str(result),
                    )
                else:
                    completed[subtask.id] = result

        return completed

    async def _dispatch(
        self, subtask: SubTask, dep_results: dict[str, Any],
        correlation_id: CorrelationId,
    ) -> SubTaskResult:
        """Route a subtask to the right specialist agent."""
        agent_type = subtask.agent_type
        if not self._registry.has(agent_type):
            _log.warning("supervisor.unknown_agent_type", event_type="agents",
                         agent_type=agent_type, fallback="general")
            agent_type = "general" if self._registry.has("general") else self._registry.agent_types[0]

        agent = self._registry.get(agent_type)
        _log.info("supervisor.dispatch", event_type="agents",
                   subtask_id=subtask.id, agent_type=agent_type,
                   description=subtask.description[:100])

        return await agent.execute(
            subtask_id=subtask.id, description=subtask.description,
            context={}, dependency_results=dep_results,
            correlation_id=correlation_id,
        )

    async def synthesize(
        self, original_task: str, results: dict[str, SubTaskResult],
        correlation_id: CorrelationId,
    ) -> str:
        """Combine all subtask results into a coherent final answer."""
        parts = []
        for sid, result in results.items():
            status = "✓" if result.ok else "✗"
            output = str(result.output)[:2000] if result.output else result.error or "no output"
            parts.append(f"[{status}] {sid} ({result.agent_type}): {output}")
        combined = "\n\n".join(parts)

        resp = await self._gw.complete(ModelRequest(
            correlation_id=correlation_id, system=_SYNTHESIZE_SYSTEM,
            prompt=f"ORIGINAL TASK: {original_task}\n\nSUBTASK RESULTS:\n{combined}",
            max_tokens=4096, temperature=0.2,
        ))
        return resp.text

    def _parse_dag(self, text: str, goal: str) -> TaskDAG:
        """Parse LLM output into a TaskDAG."""
        try:
            s, e = text.find("{"), text.rfind("}")
            if s == -1 or e == -1:
                raise ValueError("No JSON found")
            data = json.loads(text[s : e + 1])
            subtasks_raw = data.get("subtasks", [])
            subtasks = [
                SubTask(
                    id=str(st.get("id", f"step-{i}")),
                    description=str(st.get("description", "")),
                    agent_type=str(st.get("agent_type", "general")),
                    dependencies=list(st.get("dependencies", [])),
                )
                for i, st in enumerate(subtasks_raw)
            ]
            return TaskDAG(goal=goal, subtasks=subtasks)
        except (json.JSONDecodeError, ValueError) as exc:
            _log.warning("supervisor.parse_failed", event_type="agents", error=repr(exc))
            # Fallback: single-subtask DAG with 'general' agent
            return TaskDAG(goal=goal, subtasks=[
                SubTask(id="fallback", description=goal, agent_type="general"),
            ])
