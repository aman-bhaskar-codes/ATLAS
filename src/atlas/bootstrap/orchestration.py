"""Orchestration bootstrap — tools, dispatcher, events, planner, reasoning, orchestrator."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from atlas.infra.clock import Clock
from atlas.infra.config import AppConfig
from atlas.infra.db import Database
from atlas.infra.ids import CorrelationId, IdGenerator
from atlas.infra.types import AuditRecord
from atlas.intelligence.gateway import ModelGateway
from atlas.memory.retrieval import Retriever
from atlas.memory.semantic import SemanticMemory
from atlas.memory.working import WorkingMemory
from atlas.orchestration.context_builder import ContextBuilder
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
from atlas.orchestration.registry import ToolRegistry
from atlas.orchestration.replanner import Replanner
from atlas.orchestration.router import Router
from atlas.orchestration.self_critique import SelfCritique
from atlas.orchestration.tiering import TierEstimator
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
) -> OrchestrationComponents:
    """Build orchestration layer: tools, dispatcher, planner, reasoning loop, orchestrator."""

    tool_registry = ToolRegistry()
    for t in tools.values():
        tool_registry.register(t, ("read", "write", "delete", "side_effect", "read_only"))

    events = EventPublisher(bus)
    safety.set_events(events)
    retriever.set_events(events)

    router = Router(gateway)
    planner = Planner(gateway)
    context_builder = ContextBuilder(
        retriever=retriever, working=working, system_prompt="You are an autonomous agent."
    )
    parser = ResponseParser()
    validator = OutputValidator()
    prompts = PromptBuilder()
    recorder = ExecutionRecorder(episodic, clock)
    monitor = ExecutionMonitor(killswitch)
    retry = RetryManager()
    estimator = TierEstimator(classifier)

    async def critique_audit(corr: str, action: Action, critique: Critique) -> None:
        await audit.record(AuditRecord(
            correlation_id=CorrelationId(corr), ts=clock.now(), actor="critique",
            action="self_critique", tool=action.tool,
            outcome=critique.verdict.value,
            payload={"reason": critique.reason, "action": action.model_dump()},
        ))

    reflection: SelfCritique | NoOpReflection
    if config.critique.enabled:
        reflection = SelfCritique(
            gateway=gateway, estimator=estimator,
            parser=parser, validator=validator,
            correlation_id_provider=ids.correlation_id, audit=critique_audit,
            memory=semantic,
        )
    else:
        reflection = NoOpReflection()

    dispatcher = ToolDispatcher(tool_registry, safety)
    limits = ExecutionLimits(max_steps=15)

    # Phase 1: Replanner and Verifier
    replanner = Replanner(gateway)
    verifier = GoalVerifier(gateway) if config.critique.enabled else NullVerifier()

    reasoning = ReasoningLoop(
        gateway=gateway, dispatcher=dispatcher, parser=parser,
        validator=validator, prompts=prompts, recorder=recorder,
        monitor=monitor, retry=retry, reflection=reflection,
        events=events, limits=limits, working=working,
        replanner=replanner, verifier=verifier,
    )

    orchestrator = Orchestrator(
        ids=ids, clock=clock, db=db, router=router, planner=planner,
        context_builder=context_builder, reasoning=reasoning,
        registry=tool_registry, events=events,
    )

    return OrchestrationComponents(
        tool_registry=tool_registry,
        events=events,
        orchestrator=orchestrator,
    )
