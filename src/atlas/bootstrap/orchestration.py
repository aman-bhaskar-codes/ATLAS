"""Orchestration bootstrap — tools, dispatcher, events, planner, reasoning, orchestrator."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from atlas.infra.clock import Clock
from atlas.infra.config import AppConfig
from atlas.infra.db import Database
from atlas.infra.execution_store import SQLiteCancellationStore, SQLiteExecutionStore
from atlas.infra.ids import CorrelationId, IdGenerator
from atlas.infra.types import AuditRecord
from atlas.intelligence.gateway import ModelGateway
from atlas.memory.retrieval import Retriever
from atlas.memory.semantic import SemanticMemory
from atlas.memory.working import WorkingMemory
from atlas.orchestration.context_builder import ContextBuilder
from atlas.orchestration.context_engine import ContextCompactor
from atlas.orchestration.dag_executor import DagExecutor
from atlas.orchestration.dispatcher import ToolDispatcher
from atlas.orchestration.events import EventPublisher
from atlas.orchestration.goal import GoalVerifier, NullVerifier
from atlas.orchestration.limits import ExecutionLimits
from atlas.orchestration.managers.retry import RetryManager
from atlas.orchestration.monitor import ExecutionMonitor
from atlas.orchestration.orchestrator import Orchestrator
from atlas.orchestration.parser import ResponseParser
from atlas.orchestration.planner import Planner
from atlas.orchestration.prompt_builder import PromptBuilder
from atlas.orchestration.reasoning import ReasoningLoop
from atlas.orchestration.recorder import ExecutionRecorder
from atlas.orchestration.reflection import NoOpReflection
from atlas.orchestration.registry import ToolMetadata, ToolRegistry
from atlas.orchestration.replanner import Replanner
from atlas.orchestration.router import Router
from atlas.orchestration.self_critique import SelfCritique
from atlas.orchestration.tiering import TierEstimator
from atlas.orchestration.tool_routing import ToolHealthTracker, ToolRouter
from atlas.orchestration.types import Action, Critique
from atlas.orchestration.validator import OutputValidator
from atlas.safety.audit import AuditLog
from atlas.safety.classifier import TierClassifier
from atlas.safety.engine import SafetyEngine
from atlas.safety.killswitch import KillSwitch
from atlas.tools.base import Tool


@dataclass
class OrchestrationComponents:
    tool_registry: ToolRegistry
    tool_health: ToolHealthTracker
    tool_router: ToolRouter
    events: EventPublisher
    orchestrator: Orchestrator


def build_orchestration(
    *,
    config: AppConfig,
    ids: IdGenerator,
    clock: Clock,
    db: Database,
    audit: AuditLog,
    classifier: TierClassifier,
    killswitch: KillSwitch,
    safety: SafetyEngine,
    gateway: ModelGateway,
    retriever: Retriever,
    working: WorkingMemory,
    semantic: SemanticMemory,
    bus: Any,
    tools: dict[str, Tool],
    episodic: Any,
    trajectory_store: Any = None,  # Phase 2
    experience_extractor: Any = None,  # Phase 2
    skill_store: Any = None,  # Batch 4
    world_state: Any = None,  # Batch 4
) -> OrchestrationComponents:
    """Build orchestration layer: tools, dispatcher, planner, reasoning loop, orchestrator."""

    tool_registry = ToolRegistry()
    _operations = ("read", "write", "delete", "side_effect", "read_only")
    _metadata_map = {
        "filesystem": ToolMetadata(
            name="filesystem",
            operations=_operations,
            description="Read and write files within allowed paths.",
            estimated_latency_ms=50,
            idempotent=False,
            side_effects=True,
        ),
        "shell": ToolMetadata(
            name="shell",
            operations=_operations,
            description="Run allowlisted commands in a sandbox.",
            estimated_latency_ms=1500,
            idempotent=False,
            side_effects=True,
        ),
    }
    for t in tools.values():
        tool_registry.register(t, _operations, _metadata_map.get(t.name))

    events = EventPublisher(bus)
    safety.set_events(events)
    retriever.set_events(events)

    router = Router(gateway)
    planner = Planner(gateway)
    context_builder = ContextBuilder(retriever=retriever, working=working, system_prompt="You are an autonomous agent.")
    parser = ResponseParser()
    validator = OutputValidator()
    prompts = PromptBuilder()
    recorder = ExecutionRecorder(episodic, clock)
    monitor = ExecutionMonitor(killswitch)
    retry = RetryManager()
    estimator = TierEstimator(classifier)

    async def critique_audit(corr: str, action: Action, critique: Critique) -> None:
        await audit.record(
            AuditRecord(
                correlation_id=CorrelationId(corr),
                ts=clock.now(),
                actor="critique",
                action="self_critique",
                tool=action.tool,
                outcome=critique.verdict.value,
                payload={"reason": critique.reason, "action": action.model_dump()},
            )
        )

    reflection: SelfCritique | NoOpReflection
    if config.critique.enabled:
        reflection = SelfCritique(
            gateway=gateway,
            estimator=estimator,
            parser=parser,
            validator=validator,
            correlation_id_provider=ids.correlation_id,
            audit=critique_audit,
            memory=semantic,
        )
    else:
        reflection = NoOpReflection()

    tool_health = ToolHealthTracker()
    tool_router = ToolRouter(tool_registry, tool_health)
    dispatcher = ToolDispatcher(tool_registry, safety, health=tool_health)
    limits = ExecutionLimits(max_steps=15)

    # Phase 1: Replanner and Verifier
    replanner = Replanner(gateway)
    verifier = GoalVerifier(gateway) if config.critique.enabled else NullVerifier()

    reasoning = ReasoningLoop(
        gateway=gateway,
        dispatcher=dispatcher,
        parser=parser,
        validator=validator,
        prompts=prompts,
        recorder=recorder,
        monitor=monitor,
        retry=retry,
        reflection=reflection,
        events=events,
        limits=limits,
        working=working,
        replanner=replanner,
        verifier=verifier,
        trajectory_store=trajectory_store,  # Phase 2
        compactor=ContextCompactor(),  # Batch 5
    )

    orchestrator = Orchestrator(
        ids=ids,
        clock=clock,
        execution_store=SQLiteExecutionStore(db),
        cancellation_store=SQLiteCancellationStore(db),
        router=router,
        planner=planner,
        context_builder=context_builder,
        reasoning=reasoning,
        registry=tool_registry,
        events=events,
        trajectory_store=trajectory_store,  # Phase 2
        experience_extractor=experience_extractor,  # Phase 2
        skill_store=skill_store,  # Batch 4
        world_state=world_state,  # Batch 4
        dag_executor=DagExecutor(dispatcher),  # Batch 5
    )

    return OrchestrationComponents(
        tool_registry=tool_registry,
        tool_health=tool_health,
        tool_router=tool_router,
        events=events,
        orchestrator=orchestrator,
    )
