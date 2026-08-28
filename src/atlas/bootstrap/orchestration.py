"""Orchestration bootstrap — tools, dispatcher, events, planner, reasoning, orchestrator."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from atlas.infra.clock import Clock
from atlas.infra.cognition import TaskDomain
from atlas.infra.config import AppConfig
from atlas.infra.db import Database
from atlas.infra.execution_store import SQLiteCancellationStore, SQLiteExecutionStore
from atlas.infra.ids import CorrelationId, IdGenerator
from atlas.infra.types import AuditRecord
from atlas.intelligence.gateway import ModelGateway
from atlas.memory.retrieval import Retriever
from atlas.memory.semantic import SemanticMemory
from atlas.memory.working import WorkingMemory
from atlas.orchestration.agents import (
    AgentSupervisor,
    Specialist,
    Synthesizer,
    TaskDecomposer,
)
from atlas.orchestration.checkpoint import CheckpointStore, SQLiteCheckpointBackend
from atlas.orchestration.context_builder import ContextBuilder
from atlas.orchestration.context_engine import ContextCompactor
from atlas.orchestration.dag_executor import DagExecutor
from atlas.orchestration.dispatcher import ToolDispatcher
from atlas.orchestration.events import EventPublisher
from atlas.orchestration.goal import GoalVerifier, NullVerifier, Verifier
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
from atlas.orchestration.self_critique import SelfCritique
from atlas.orchestration.tiering import TierEstimator
from atlas.orchestration.tool_routing import ToolHealthTracker, ToolRouter
from atlas.orchestration.types import Action, Critique
from atlas.orchestration.understanding import IntentExtractor
from atlas.orchestration.validator import OutputValidator
from atlas.orchestration.verification import (
    CommandVerifier,
    DomainVerifierRouter,
    FilesystemStateVerifier,
    GroundingVerifier,
)
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
    checkpoints: CheckpointStore


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
    llm_tracker: Any = None,  # LLMCallTracker for trajectory cost tracking
    trajectory_store: Any = None,  # Phase 2
    experience_extractor: Any = None,  # Phase 2
    skill_store: Any = None,  # Batch 4
    world_state: Any = None,  # Batch 4
) -> OrchestrationComponents:
    """Build orchestration layer: tools, dispatcher, planner, reasoning loop, orchestrator."""

    tool_registry = ToolRegistry()
    _fs_operations = ("read", "list", "search", "inspect", "tree", "stat", "write", "delete", "overwrite")
    _shell_operations = ("read_only", "side_effect")
    _metadata_map = {
        "filesystem": ToolMetadata(
            name="filesystem",
            operations=_fs_operations,
            description=(
                "Read, list, search, and write files within allowed paths. "
                "Operations: read (read file content), list (list directory entries), "
                "search (text search), inspect/tree/stat (directory metadata), "
                "write (create/overwrite), delete (remove)."
            ),
            estimated_latency_ms=50,
            idempotent=False,
            side_effects=True,
        ),
        "shell": ToolMetadata(
            name="shell",
            operations=_shell_operations,
            description=(
                "Run allowlisted shell commands. Operations: read_only "
                "(safe cmds: ls, cat, grep, find, git log), side_effect "
                "(modifying cmds, requires confirmation)."
            ),
            estimated_latency_ms=1500,
            idempotent=False,
            side_effects=True,
        ),
    }
    for t in tools.values():
        ops = (
            _fs_operations
            if t.name == "filesystem"
            else _shell_operations
            if t.name == "shell"
            else ("read", "write", "delete")
        )
        tool_registry.register(t, ops, _metadata_map.get(t.name))

    events = EventPublisher(bus)
    safety.set_events(events)
    retriever.set_events(events)

    # Phase 2: the one understanding stage. Replaces Router, which made a
    # second model call to classify what this now derives from the intent.
    understanding = IntentExtractor(gateway)
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

    checkpoint_store = CheckpointStore(SQLiteCheckpointBackend(db, ids), clock)  # Batch 7
    tool_health = ToolHealthTracker()
    tool_router = ToolRouter(tool_registry, tool_health)
    dispatcher = ToolDispatcher(tool_registry, safety, health=tool_health)
    limits = ExecutionLimits(max_steps=15)

    # Phase 1/12: Replanner and capability-aware verification.
    replanner = Replanner(gateway)
    # WHY annotated: every arm must satisfy the Verifier protocol, and mypy
    # would otherwise infer the union of the concrete classes.
    verifier: Verifier
    if not config.verification.enabled:
        verifier = NullVerifier()
    else:
        # The general-purpose fallback: model judgement against the intent's
        # success criteria. Domains that can be checked mechanically override it.
        default_verifier = GoalVerifier(gateway, min_pass_score=config.verification.min_pass_score)
        by_domain: dict[TaskDomain, Verifier] = {}
        # Coding work is verified by running the configured check command
        # (tests/lint), not by asking the model whether it worked. Only wired
        # when an operator has configured a command — an empty command must not
        # masquerade as a verifier.
        if config.verification.command:
            by_domain[TaskDomain.CODING] = CommandVerifier(
                dispatcher,
                command=config.verification.command,
                timeout_s=config.verification.command_timeout_s,
            )
        # Filesystem work is verified by re-reading the affected paths.
        if "filesystem" in tools:
            by_domain[TaskDomain.FILESYSTEM] = FilesystemStateVerifier(dispatcher)
        # Research and self-knowledge answers must be grounded in evidence the
        # run actually gathered. GroundingVerifier fails an ungrounded answer
        # and returns not_applicable for a grounded one — at which point the
        # router's deferral hands it to the judge for criteria evaluation.
        grounding = GroundingVerifier()
        by_domain[TaskDomain.RESEARCH] = grounding
        by_domain[TaskDomain.SELF_KNOWLEDGE] = grounding
        verifier = DomainVerifierRouter(default=default_verifier, by_domain=by_domain)

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
        checkpoint_store=checkpoint_store,  # Batch 7
    )

    orchestrator = Orchestrator(
        ids=ids,
        clock=clock,
        execution_store=SQLiteExecutionStore(db),
        cancellation_store=SQLiteCancellationStore(db),
        understanding=understanding,
        planner=planner,
        context_builder=context_builder,
        reasoning=reasoning,
        registry=tool_registry,
        events=events,
        llm_tracker=llm_tracker,  # Batch 10: cost tracking
        trajectory_store=trajectory_store,  # Phase 2
        experience_extractor=experience_extractor,  # Phase 2
        skill_store=skill_store,  # Batch 4
        world_state=world_state,  # Batch 4
        dag_executor=DagExecutor(dispatcher),  # Batch 5
        supervisor=_build_supervisor(config, gateway, reasoning, events),
    )

    return OrchestrationComponents(
        tool_registry=tool_registry,
        tool_health=tool_health,
        tool_router=tool_router,
        events=events,
        orchestrator=orchestrator,
        checkpoints=checkpoint_store,
    )


def _build_supervisor(
    config: AppConfig,
    gateway: ModelGateway,
    reasoning: ReasoningLoop,
    events: EventPublisher,
) -> AgentSupervisor | None:
    """Assemble the multi-agent layer, or return None when it is disabled.

    Returning None (rather than a no-op supervisor) is deliberate: the
    Orchestrator skips the delegation branch entirely, so a disabled agents
    layer costs nothing at runtime — not even a config lookup per task.

    The specialist reuses the SAME ReasoningLoop instance the serial path uses.
    That is what guarantees delegated tool calls cannot skip the SafetyEngine:
    there is only ever one loop, one dispatcher, one funnel.
    """
    cfg = config.agents
    if not cfg.enabled:
        return None
    return AgentSupervisor(
        decomposer=TaskDecomposer(
            gateway,
            max_subtasks=cfg.max_subtasks,
            min_subtasks=cfg.min_subtasks,
            max_steps_per_subtask=cfg.max_steps_per_subtask,
        ),
        specialist=Specialist(
            reasoning,
            max_tokens_per_subtask=cfg.max_tokens_per_subtask,
            max_runtime_s=cfg.subtask_runtime_s,
        ),
        synthesizer=Synthesizer(gateway, max_tokens=cfg.synthesis_max_tokens),
        events=events,
        max_concurrency=cfg.max_concurrency,
        deadline_s=cfg.deadline_s,
    )
