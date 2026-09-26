"""Execution bootstrap — the durable execution fabric as a subsystem (Part 4).

Wires the ExecutionEngine from real subsystems: the Part-1 ToolingExecutor as
the governed tool funnel, the Part-2 catalog for staleness revalidation, the
Part-3 judgment cascade for bounded recovery judgment, the Part-3 routing
engine as the REPLAN hook (a replan re-routes the same objective), and the
SQLite run store + MessageBus for durability and events.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from atlas.infra.bus import MessageBus
from atlas.infra.config import AppConfig
from atlas.infra.db import Database
from atlas.infra.logging import get_logger
from atlas.tooling.catalog.catalog import ToolCatalog
from atlas.tooling.execution.engine import ExecutionEngine
from atlas.tooling.execution.judgment_support import build_execution_cascade
from atlas.tooling.execution.store import ExecutionRunStore

_log = get_logger("atlas.bootstrap.execution")


@dataclass
class ExecutionComponents:
    engine: ExecutionEngine
    config: Any


def build_execution(
    *,
    config: AppConfig,
    db: Database,
    tooling_executor: Any,
    catalog: ToolCatalog | None,
    routing_engine: Any | None = None,
    gateway: Any | None = None,
    bus: MessageBus | None = None,
) -> ExecutionComponents:
    exec_cfg = config.execution

    cascade = (
        build_execution_cascade(config, gateway=gateway)
        if (config.routing.enable_jev or config.routing.enable_llm_judgment)
        else None
    )

    replan_hook = None
    if routing_engine is not None:

        async def replan_hook(run: Any, step: Any, failure: str) -> Any:
            """A replan re-routes the SAME objective through the Part-3 engine
            (§95/§96) — bounded by the controller's max_replans."""
            task = routing_engine.normalize_request(
                objective=str(step.input_mapping.get("objective", failure)),
                task_id=run.task_id,
                correlation_id=run.correlation_id,
                source="system",
            )
            result = await routing_engine.route(task)
            if result.decision.decision_type == "route" and result.plan is not None:
                routing_engine.attach_decision(result.plan.plan_id, result.decision)
                return result.plan
            return None

    engine = ExecutionEngine(
        config=exec_cfg,
        store=ExecutionRunStore(db),
        tooling_executor=tooling_executor,
        catalog=catalog,
        cascade=cascade,
        bus=bus,
        replan_hook=replan_hook,
        workflow_depth=exec_cfg.max_nested_workflows,
    )
    _log.info(
        "execution.ready",
        event_type="lifecycle",
        max_concurrent_steps=exec_cfg.max_concurrent_steps,
        checkpoint_enabled=exec_cfg.checkpoint_enabled,
        max_replans=exec_cfg.max_replans,
    )
    return ExecutionComponents(engine=engine, config=exec_cfg)


__all__ = ["ExecutionComponents", "build_execution"]
