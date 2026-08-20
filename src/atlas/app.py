"""Composition root.

WHY one place: dependency wiring is centralized so the object graph is auditable
in a single file and no module self-constructs its dependencies. WHY bootstrap
modules: each bootstrap module owns one concern; app.py delegates and combines.
The Atlas dataclass is the single surface handed to every interface (API, CLI).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from atlas.bootstrap.runtime import RuntimeSupervisor, SystemState
from atlas.capabilities.browser.builder import build_browser_platform
from atlas.capabilities.browser.platform import BrowserPlatform
from atlas.capabilities.dispatcher import CapabilityDispatcher
from atlas.capabilities.identity.platform import IdentityPlatform
from atlas.capabilities.notification.builder import build_notification_platform
from atlas.capabilities.notification.platform import NotificationPlatform
from atlas.capabilities.observability.telemetry import CapabilityTelemetry
from atlas.capabilities.platforms.calendar_platform import CalendarPlatform
from atlas.capabilities.platforms.contacts_platform import ContactsPlatform
from atlas.capabilities.platforms.currency_platform import CurrencyPlatform
from atlas.capabilities.platforms.email_platform import EmailPlatform
from atlas.capabilities.platforms.knowledge_platform import KnowledgePlatform
from atlas.capabilities.platforms.location_platform import LocationPlatform
from atlas.capabilities.platforms.weather_platform import WeatherPlatform
from atlas.capabilities.registry.capability import CapabilityRegistry
from atlas.capabilities.registry.health import CapabilityHealth
from atlas.capabilities.registry.provider_registry import ProviderRegistry as CapProviderRegistry
from atlas.capabilities.router import CapabilityRouter as ExtCapabilityRouter
from atlas.infra.bus import MessageBus
from atlas.infra.clock import Clock
from atlas.infra.config import AppConfig, Settings, load_app_config, load_permissions, load_settings, resolve_master_key
from atlas.infra.db import Database
from atlas.infra.feedback import FeedbackStore
from atlas.infra.ids import IdGenerator
from atlas.infra.lifecycle import Lifecycle
from atlas.infra.llm_tracker import LLMCallTracker
from atlas.infra.logging import configure_logging, get_logger
from atlas.infra.metrics import Metrics
from atlas.infra.registry import ServiceRegistry
from atlas.infra.scheduler import CronScheduler
from atlas.infra.tracing import Tracer
from atlas.infra.workflows import WorkflowStore
from atlas.intelligence.gateway import ModelGateway
from atlas.interfaces.notify import CliConfirmer, CompositeConfirmer
from atlas.memory.consolidation import Consolidator
from atlas.memory.embedder import EmbeddingWorker, OllamaEmbedder
from atlas.memory.episodic import EpisodicMemory
from atlas.memory.knowledge_store import KnowledgeStore
from atlas.memory.pruning import Pruner
from atlas.memory.retrieval import Retriever
from atlas.memory.semantic import SemanticMemory
from atlas.memory.user_model import UserModel
from atlas.memory.vectorstore import ChromaVectorStore
from atlas.memory.working import WorkingMemory
from atlas.orchestration.orchestrator import Orchestrator
from atlas.safety.audit import AuditLog
from atlas.safety.classifier import TierClassifier
from atlas.safety.engine import SafetyEngine
from atlas.safety.killswitch import KillSwitch
from atlas.safety.manifest import Manifest, load_manifest
from atlas.safety.sandbox_docker import DockerSandbox, SandboxSpec
from atlas.safety.sandbox_native import NativeSandbox
from atlas.tools.base import Tool
from atlas.tools.filesystem import FilesystemTool
from atlas.tools.shell import ShellTool

_CONFIG_DIR = Path(__file__).resolve().parents[2] / "config"
_REPO_ROOT = Path(__file__).resolve().parents[2]
_log = get_logger("atlas.app")


def _validate_repo_root(root: Path) -> None:
    """Assert repo root actually contains the expected project markers."""
    if not (root / "pyproject.toml").exists():
        _log.warning(
            "repo_root.suspect",
            event_type="lifecycle",
            root=str(root),
            detail="pyproject.toml not found — sandbox mount may be incorrect",
        )


_validate_repo_root(_REPO_ROOT)


@dataclass
class Atlas:
    settings: Settings
    config: AppConfig
    manifest: Manifest
    db: Database
    registry: ServiceRegistry
    lifecycle: Lifecycle
    ids: IdGenerator
    clock: Clock
    metrics: Metrics
    tracer: Tracer
    audit: AuditLog
    killswitch: KillSwitch
    classifier: TierClassifier
    safety: SafetyEngine
    tools: dict[str, Tool]
    gateway: ModelGateway
    notification_platform: NotificationPlatform
    vectors: ChromaVectorStore
    embedder: OllamaEmbedder
    embedding_worker: EmbeddingWorker
    episodic: EpisodicMemory
    semantic: SemanticMemory
    user_model: UserModel
    working: WorkingMemory
    retriever: Retriever
    consolidator: Consolidator
    pruner: Pruner
    knowledge_store: KnowledgeStore
    bus: MessageBus
    orchestrator: Orchestrator
    cap_registry: CapabilityRegistry
    cap_health: CapabilityHealth
    cap_providers: CapProviderRegistry
    ext_cap_router: ExtCapabilityRouter
    cap_dispatcher: CapabilityDispatcher
    cap_telemetry: CapabilityTelemetry
    identity: IdentityPlatform
    knowledge_platform: KnowledgePlatform
    email_platform: EmailPlatform
    calendar_platform: CalendarPlatform
    contacts_platform: ContactsPlatform
    weather_platform: WeatherPlatform
    location_platform: LocationPlatform
    currency_platform: CurrencyPlatform
    # Optional/phase-dependent fields (with defaults)
    trajectory_store: Any = None  # Phase 2: TrajectoryStore
    experience_extractor: Any = None  # Phase 2: ExperienceExtractor
    browser_platform: BrowserPlatform | None = None
    feedback: FeedbackStore | None = None
    scheduler: CronScheduler | None = None
    llm_tracker: LLMCallTracker | None = None
    workflows: WorkflowStore | None = None
    skill_store: Any = None  # Batch 4
    strategy_store: Any = None  # Batch 4
    world_state: Any = None  # Batch 4
    skill_promoter: Any = None  # Batch 4
    tool_router: Any = None  # Batch 6: operator surface
    tool_health: Any = None  # Batch 6
    checkpoints: Any = None  # Batch 7
    model_registry: Any = None  # Model registry for frontend
    runtime_supervisor: RuntimeSupervisor | None = None  # Runtime orchestration layer

    async def start(self) -> Any:
        if self.runtime_supervisor is not None and self.runtime_supervisor.state in {
            SystemState.READY,
            SystemState.DEGRADED,
            SystemState.BUSY,
        }:
            return

        # The supervisor verifies infrastructure during its startup phases. The
        # lifecycle owns the database connection, so it must run first.
        await self.lifecycle.start()

        # These subscriptions and the durable event processor are runtime-wide,
        # not API-only. Establish them before readiness is reported.
        self.episodic.set_bus(self.bus)
        self.semantic.set_bus(self.bus)
        self.user_model.set_bus(self.bus)
        self.knowledge_store.set_bus(self.bus)
        await self.bus.start()

        # Initialize runtime supervisor if not already initialized
        if self.runtime_supervisor is None:
            self.runtime_supervisor = RuntimeSupervisor(
                settings=self.settings,
                config=self.config,
                clock=self.clock,
                metrics=self.metrics,
            )
        
        # Use runtime supervisor for managed startup
        health_report = await self.runtime_supervisor.start(self)
        
        # Batch 7: fail-clean recovery for tasks orphaned by a previous crash.
        from atlas.orchestration.recovery import recover_interrupted_tasks

        if self.checkpoints is not None:
            await recover_interrupted_tasks(
                self.db,
                self.checkpoints,
                self.clock,
                live_task_ids=frozenset(),
            )
        return health_report

    async def close(self) -> None:
        # Use runtime supervisor for managed shutdown if available
        if self.runtime_supervisor is not None:
            await self.runtime_supervisor.shutdown()
        
        # Legacy shutdown for compatibility (will be phased out)
        await self.embedding_worker.stop()
        if self.scheduler is not None:
            await self.scheduler.stop()
        if self.browser_platform is not None:
            await self.browser_platform.shutdown()
        # Close bus first so background queue-processor exits before DB closes
        await self.bus.close()
        await self.embedder.close()
        await self.gateway.close()
        await self.db.stop()
        await self.lifecycle.stop()

    async def __aenter__(self) -> Atlas:
        await self.start()
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.close()


async def build(config_dir: Path = _CONFIG_DIR) -> Atlas:
    settings = load_settings()
    config = load_app_config(config_dir)
    manifest = load_manifest(load_permissions(config_dir))

    configure_logging(config.logging)

    # ── Infrastructure ────────────────────────────────────────────── #
    from atlas.bootstrap.infrastructure import build_infrastructure

    infra = build_infrastructure(settings, config)
    ids, clock, metrics, tracer = infra.ids, infra.clock, infra.metrics, infra.tracer
    db, registry, lifecycle, bus = infra.db, infra.registry, infra.lifecycle, infra.bus
    audit, killswitch = infra.audit, infra.killswitch

    # ── Safety ───────────────────────────────────────────────────── #
    from atlas.bootstrap.safety import build_safety

    saf = build_safety(
        config=config,
        manifest=manifest,
        audit=audit,
        killswitch=killswitch,
        clock=clock,
        ids=ids,
    )
    classifier, safety, cap_audit = saf.classifier, saf.safety, saf.cap_audit

    # ── Identity ─────────────────────────────────────────────────── #
    master_key = resolve_master_key(settings)

    # ── Intelligence ──────────────────────────────────────────────── #
    from atlas.bootstrap.intelligence import build_intelligence

    intel = await build_intelligence(
        settings=settings,
        config=config,
        config_dir=config_dir,
        db=db,
        ids=ids,
        clock=clock,
        audit=audit,
    )
    gateway, embedder, llm_tracker = intel.gateway, intel.embedder, intel.llm_tracker

    # ── Capability infrastructure ─────────────────────────────────── #
    cap_registry = CapabilityRegistry()
    cap_health = CapabilityHealth()
    cap_providers = CapProviderRegistry(cap_health)
    ext_cap_router = ExtCapabilityRouter(gateway)
    cap_telemetry = CapabilityTelemetry(cap_audit)
    cap_dispatcher = CapabilityDispatcher(
        registry=cap_registry,
        providers=cap_providers,
        health=cap_health,
        safety=safety,
        telemetry=cap_telemetry,
    )

    # ── Memory ────────────────────────────────────────────────────── #
    from atlas.bootstrap.memory import build_memory

    mem = build_memory(
        settings=settings,
        db=db,
        ids=ids,
        clock=clock,
        embedder=embedder,
        gateway=gateway,
    )
    vectors = mem.vectors
    embedding_worker = mem.embedding_worker
    episodic, semantic = mem.episodic, mem.semantic
    user_model, working = mem.user_model, mem.working
    knowledge_store = mem.knowledge_store
    retriever, consolidator, pruner = mem.retriever, mem.consolidator, mem.pruner
    trajectory_store, experience_extractor = mem.trajectory_store, mem.experience_extractor  # Phase 2
    skill_store, strategy_store = mem.skill_store, mem.strategy_store  # Batch 4
    world_state, skill_promoter = mem.world_state, mem.skill_promoter  # Batch 4

    # ── Capability platforms ──────────────────────────────────────── #
    from atlas.bootstrap.capabilities import build_data_platforms, build_identity_platform

    # Notification adapter for safety confirmer
    class NotificationPlatformAdapter:
        def __init__(self, platform: Any, clock: Clock, ids: IdGenerator) -> None:
            self._platform = platform
            self._clock = clock
            self._ids = ids

        async def notify(self, title: str, body: str, *, priority: int = 3) -> None:
            from atlas.capabilities.notification.domain.models import (
                Notification,
                NotificationKind,
                NotificationPriority,
            )

            p = NotificationPriority(priority) if priority in (0, 1, 2, 3) else NotificationPriority.NORMAL
            n = Notification(
                id=self._ids.execution_id(),
                correlation_id=self._ids.correlation_id(),
                kind=NotificationKind.WARNING,
                priority=p,
                title=title,
                body=body,
                urgent=True,
                created_ts=self._clock.now(),
            )
            await self._platform.notify(n)

        async def ask(self, title: str, body: str, *, timeout_s: float) -> bool | None:
            from atlas.capabilities.notification.domain.models import ApprovalRequest

            req = ApprovalRequest(
                id=self._ids.execution_id(),
                correlation_id=self._ids.correlation_id(),
                prompt=title,
                detail=body,
                timeout_s=timeout_s,
                default_on_timeout=False,
            )
            decision = await self._platform.request_approval(req, channels=())
            if decision.timed_out:
                return None
            return bool(decision.approved)

    # Build identity first (needed by notification)
    identity_platform = build_identity_platform(
        db=db,
        cap_audit=cap_audit,
        master_key=master_key,
    )

    # Build notification (requires identity)
    notification_platform = build_notification_platform(
        config_dir=config_dir,
        db=db,
        clock=clock,
        ids=ids,
        gateway=gateway,
        identity=identity_platform,
        callback_base=settings.ntfy_callback_base,
    )

    # Build data platforms (require identity and notification)
    data_platforms = await build_data_platforms(
        config=config,
        config_dir=config_dir,
        db=db,
        ids=ids,
        clock=clock,
        gateway=gateway,
        retriever=retriever,
        episodic=episodic,
        identity=identity_platform,
        notification_platform=notification_platform,
        cap_registry=cap_registry,
        cap_providers=cap_providers,
    )
    knowledge_platform = data_platforms.knowledge
    email_platform = data_platforms.email
    calendar_platform = data_platforms.calendar
    contacts_platform = data_platforms.contacts
    weather_platform = data_platforms.weather_platform
    location_platform = data_platforms.location_platform
    currency_platform = data_platforms.currency_platform
    _ = data_platforms.known_contacts  # Reserved for future contact-aware features

    # ── Sandboxed tools ───────────────────────────────────────────── #
    docker_sandbox = DockerSandbox(
        SandboxSpec(
            image=config.sandbox.image,
            cpus=config.sandbox.cpus,
            memory=config.sandbox.memory,
            pids_limit=config.sandbox.pids_limit,
            workdir=config.sandbox.workdir,
        )
    )
    _docker_ok = await docker_sandbox.health()
    if _docker_ok:
        sandbox = docker_sandbox
        _log.info("sandbox.docker", event_type="lifecycle", detail="Docker available")
    elif settings.env == "dev":
        sandbox = NativeSandbox(env=settings.env)  # type: ignore[assignment]
        _log.warning(
            "sandbox.native", event_type="lifecycle", detail="Docker unavailable — using native sandbox (dev only)"
        )
    else:
        # Docker is required in non-dev environments, but the health check failed.
        # Raise a fatal error instead of assigning a broken sandbox that will fail
        # at runtime when the first tool runs. This fails fast with a clear message.
        from atlas.infra.errors import FatalError

        _log.error(
            "sandbox.docker_required",
            event_type="lifecycle",
            detail="Docker is required in non-dev environments but is unavailable",
        )
        raise FatalError(
            "Docker sandbox required but unavailable in non-dev environment. "
            "Either start Docker, set ATLAS_ENV=dev, or configure a permitted sandbox."
        )

    ws = str(_REPO_ROOT)
    tools: dict[str, Tool] = {
        "filesystem": FilesystemTool(
            read_globs=manifest.allowed_paths.get("read", []),
            write_globs=manifest.allowed_paths.get("write", []),
            sandbox=sandbox,
        ),
        "shell": ShellTool(
            read_only=manifest.allowed_commands.get("read_only", []),
            side_effect=manifest.allowed_commands.get("side_effect", []),
            sandbox=sandbox,
            mounts={ws: "/work"},
        ),
    }

    # ── Browser platform (optional) ───────────────────────────────── #
    browser_platform: BrowserPlatform | None = None
    if config.browser.enabled:
        browser_platform = build_browser_platform(
            ids=ids,
            notifications=notification_platform,
            approval_channels=tuple(),  # approval_channels defined in data_platforms builder
            safe_browsing_api_key=settings.safe_browsing_api_key,
            virustotal_api_key=settings.virustotal_api_key,
        )
        from atlas.tools.browser import BrowserTool
        tools["browser"] = BrowserTool(platform=browser_platform, ids=ids)

    notifier_adapter = NotificationPlatformAdapter(notification_platform, clock, ids)
    active_notifier = notifier_adapter if settings.ntfy_topic else None
    safety.set_confirmer(CompositeConfirmer(active_notifier, CliConfirmer(), config.notify.confirm_timeout_s))

    # ── Orchestration ─────────────────────────────────────────────── #
    from atlas.bootstrap.orchestration import build_orchestration

    orch = build_orchestration(
        config=config,
        ids=ids,
        clock=clock,
        db=db,
        audit=audit,
        classifier=classifier,
        killswitch=killswitch,
        safety=safety,
        gateway=gateway,
        retriever=retriever,
        working=working,
        semantic=semantic,
        bus=bus,
        tools=tools,
        episodic=episodic,
        llm_tracker=llm_tracker,  # Batch 10.3: cost tracking for trajectories
        trajectory_store=trajectory_store,  # Phase 2
        experience_extractor=experience_extractor,  # Phase 2
        skill_store=skill_store,  # Batch 4
        world_state=world_state,  # Batch 4
    )
    orchestrator = orch.orchestrator
    tool_router, tool_health = orch.tool_router, orch.tool_health  # Batch 6
    checkpoints = orch.checkpoints  # Batch 7

    # ── Feedback, Scheduler, Workflows ───────────────────────────── #
    feedback_store = FeedbackStore(db=db, ids=ids, clock=clock)
    cron_scheduler = CronScheduler(db=db, ids=ids, clock=clock)
    workflow_store = WorkflowStore(db=db, ids=ids, clock=clock)
    # NOTE: llm_tracker constructed in build_intelligence before InferenceRuntime

    # Phase 0: Schedule memory consolidation at 2 AM daily
    async def _consolidate_job() -> None:
        try:
            stats = await consolidator.run()
            _log.info("consolidation.scheduled_run", event_type="lifecycle", stats=str(stats))
        except Exception as exc:
            _log.error("consolidation.scheduled_error", event_type="lifecycle", error=repr(exc))
        # Batch 4: promote proven experiences into candidate skills nightly.
        try:
            created = await skill_promoter.promote_from_experiences()
            if created:
                _log.info("skill.promotion_run", event_type="lifecycle", created=len(created))
        except Exception as exc:
            _log.error("skill.promotion_error", event_type="lifecycle", error=repr(exc))

    cron_scheduler.register_job(name="memory_consolidation", cron="0 2 * * *", fn=_consolidate_job)

    _log.info("core.ready", event_type="lifecycle", providers=str(type(gateway)))
    
    # Initialize runtime supervisor (will be started in Atlas.start())
    runtime_supervisor = RuntimeSupervisor(
        settings=settings,
        config=config,
        clock=clock,
        metrics=metrics,
    )
    
    return Atlas(
        settings=settings,
        config=config,
        manifest=manifest,
        db=db,
        registry=registry,
        lifecycle=lifecycle,
        ids=ids,
        clock=clock,
        metrics=metrics,
        tracer=tracer,
        audit=audit,
        killswitch=killswitch,
        classifier=classifier,
        safety=safety,
        tools=tools,
        gateway=gateway,
        notification_platform=notification_platform,
        vectors=vectors,
        embedder=embedder,
        embedding_worker=embedding_worker,
        episodic=episodic,
        semantic=semantic,
        user_model=user_model,
        working=working,
        retriever=retriever,
        consolidator=consolidator,
        pruner=pruner,
        knowledge_store=knowledge_store,
        trajectory_store=trajectory_store,
        experience_extractor=experience_extractor,  # Phase 2
        bus=bus,
        orchestrator=orchestrator,
        cap_registry=cap_registry,
        cap_health=cap_health,
        cap_providers=cap_providers,
        ext_cap_router=ext_cap_router,
        cap_dispatcher=cap_dispatcher,
        cap_telemetry=cap_telemetry,
        runtime_supervisor=runtime_supervisor,  # Runtime orchestration layer
        identity=identity_platform,
        knowledge_platform=knowledge_platform,
        email_platform=email_platform,
        calendar_platform=calendar_platform,
        contacts_platform=contacts_platform,
        weather_platform=weather_platform,
        location_platform=location_platform,
        currency_platform=currency_platform,
        browser_platform=browser_platform,
        feedback=feedback_store,
        scheduler=cron_scheduler,
        llm_tracker=llm_tracker,
        workflows=workflow_store,
        skill_store=skill_store,  # Batch 4
        strategy_store=strategy_store,  # Batch 4
        world_state=world_state,  # Batch 4
        skill_promoter=skill_promoter,  # Batch 4
        tool_router=tool_router,  # Batch 6
        tool_health=tool_health,  # Batch 6
        checkpoints=checkpoints,  # Batch 7
        model_registry=intel.registry,  # Model registry for frontend
    )
