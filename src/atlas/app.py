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
from atlas.infra.routing_backends import BackendRouter
from atlas.infra.scheduler import CronScheduler
from atlas.infra.tracing import Tracer
from atlas.infra.workflows import WorkflowStore
from atlas.intelligence.gateway import ModelGateway
from atlas.interfaces.notify import CliConfirmer, CompositeConfirmer
from atlas.memory.consolidation import Consolidator
from atlas.memory.embedder import Embedder, EmbeddingWorker
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
    router: BackendRouter
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
    embedder: Embedder
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
    tooling: Any = None  # Universal tooling fabric (foundation part)
    routing: Any = None  # Routing fabric (Part 3): engine + registries
    execution_engine: Any = None  # Durable execution fabric (Part 4)
    mcp_manager: Any = None  # MCP runtime (Part 5)
    checkpoints: Any = None  # Batch 7
    model_registry: Any = None  # Model registry for frontend
    runtime_supervisor: RuntimeSupervisor | None = None  # Runtime orchestration layer
    computer_use: Any = None  # Prompt 2: ComputerUseComponents (engine + tool + env report)
    public_api: Any = None  # Prompt 2: PublicAPIPlatform (catalog → validation → execution)
    knowledge_fabric: Any = None  # Prompt 3: KnowledgeFabricComponents (fabric + bridges + research)
    voice_service: Any = None  # Optional voice pipeline (VoiceService or None when disabled)
    ide_service: Any = None  # Optional ADE (IDEService or None when disabled/no filesystem tool)
    agent_engine: Any = None  # Optional autonomous agent-run surface (AgentRunService or None)
    research: Any = None  # Optional Perplexity-class research surface (ResearchService or None)
    curated: Any = None  # CuratedMemory — the always-loaded MEMORY/USER tier
    lane_one: Any = None  # LaneOneRecall — default read path (SQL, no embeddings)
    intents: Any = None  # IntentStore — prospective memory ("remember to X when Y")

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

        # Storage Backbone (T3/T4): provision the schema on any Postgres account a
        # StorageDomain routes to, using the lifecycle-owned router (its pool is
        # then reused by the stores). Pure no-op under zero-config — every domain
        # resolves to SQLite, which Database already provisioned above.
        from atlas.infra.schema_provisioner import SchemaProvisioner

        await SchemaProvisioner(self.router).provision()

        # Ensure both curated surfaces exist before anything reads or swaps them.
        # Consolidation compare-and-swaps on a content hash, so it needs a row to
        # swap against; creating them here means the very first sweep on a fresh
        # install behaves like every later one instead of taking a special path.
        if self.curated is not None:
            from atlas.memory.curated import MEMORY_KEY, USER_KEY

            for key in (MEMORY_KEY, USER_KEY):
                await self.curated.create_if_absent(key)

        # These subscriptions and the durable event processor are runtime-wide,
        # not API-only. Establish them before readiness is reported.
        self.episodic.set_bus(self.bus)
        self.semantic.set_bus(self.bus)
        self.user_model.set_bus(self.bus)
        self.knowledge_store.set_bus(self.bus)
        if self.working is not None:
            self.working.set_bus(self.bus)
        if self.trajectory_store is not None:
            self.trajectory_store.set_bus(self.bus)
        await self.bus.start()

        # Part 2: reconcile the live tooling registry into the persistent tool
        # catalog (load what survived from previous runs, then sync). Runs
        # after the database + bus are live; failure-isolated per source and
        # never blocks startup.
        if self.tooling is not None:
            await self.tooling.sync_catalog()

        # Part 5: eager MCP servers connect + discover before READY (§64).
        if self.mcp_manager is not None:
            await self.mcp_manager.start_eager()

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
        if self.computer_use is not None:
            await self.computer_use.engine.shutdown()
        if self.voice_service is not None:
            await self.voice_service.close()
        if self.mcp_manager is not None:
            await self.mcp_manager.shutdown()
        if self.tooling is not None:
            await self.tooling.shutdown()
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
    router = infra.router  # the single persistence seam (T4); lifecycle-owned

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
    curated, lane_one, intents = mem.curated, mem.lane_one, mem.intents
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

    # ── Knowledge Fabric (Prompt 3) — ONE pipeline over all sources ── #
    from atlas.bootstrap.knowledge_fabric import build_knowledge_fabric

    try:
        knowledge_fabric = await build_knowledge_fabric(
            db=db,
            ids=ids,
            clock=clock,
            gateway=gateway,
            embedder=embedder,
            vectors=vectors,
            memory_retriever=retriever,
            providers=knowledge_platform.providers,
        )
    except Exception as exc:  # fabric failure degrades, never blocks startup
        _log.error("knowledge_fabric.unavailable", event_type="lifecycle", error=repr(exc))
        knowledge_fabric = None

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
        from atlas.bootstrap.browser import build_browser
        browser_platform = build_browser(
            config=config.browser,
            ids=ids,
            notifications=notification_platform,
            safety=safety,
            approval_channels=tuple(),  # approval_channels defined in data_platforms builder
            safe_browsing_api_key=settings.safe_browsing_api_key,
            virustotal_api_key=settings.virustotal_api_key,
        )
        from atlas.tools.browser import BrowserTool

        # build_browser returns None only when disabled; we are inside the
        # enabled branch, so the platform is present. Assert the invariant so
        # BrowserTool (which requires a non-None platform) type-checks.
        assert browser_platform is not None
        tools["browser"] = BrowserTool(platform=browser_platform, ids=ids)

    # ── Computer use (universal perception/control across bodies) ──── #
    from atlas.bootstrap.computer_use import build_computer_use

    computer_use = await build_computer_use(browser_platform=browser_platform)
    tools["computer_use"] = computer_use.tool
    public_api = computer_use.public_api

    # ── Knowledge / research tool ─────────────────────────────────── #
    # WHY here: the fabric is built above but was unreachable by the agent — no
    # tool meant the reasoning loop could not search, research or cite. The tool
    # goes through the ordinary Tool contract so every research action passes the
    # SafetyEngine funnel, under the `knowledge` seat permissions.yaml reserves.
    if knowledge_fabric is not None:
        from atlas.knowledge.deletion import DeletionScope
        from atlas.knowledge.domain import SourceType
        from atlas.tools.research import HttpTextFetcher, ResearchTool

        tools["knowledge"] = ResearchTool(
            fabric=knowledge_fabric.fabric,
            research=knowledge_fabric.research,
            supervisor=knowledge_fabric.supervisor,
            pipeline=knowledge_fabric.pipeline,
            fetch=HttpTextFetcher(),
            # The tool layer sits below `knowledge` and cannot name this enum,
            # so the composition root supplies the member.
            web_source_type=SourceType.WEB_PAGE,
            # The forget coordinator + the scope factory (str -> DeletionScope),
            # injected for the same layering reason as web_source_type.
            memory=knowledge_fabric.research_memory,
            deletion_scope=DeletionScope,
        )

    # ── Voice pipeline (optional; off by default) ─────────────────── #
    from atlas.bootstrap.voice import build_voice

    voice = build_voice(settings, config)

    # ── ADE / IDE (optional; off by default) ──────────────────────── #
    # Reuses the SAME safety funnel + filesystem tool as every other dispatch,
    # so the IDE is never a side door around policy (Constitution).
    from atlas.bootstrap.ide import build_ide

    ide = build_ide(
        settings,
        config,
        safety=safety,
        filesystem_tool=tools.get("filesystem"),
        ids=ids,
        clock=clock,
        db=db,
        command_tool=tools.get("shell"),
        router=router,
    )

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

    # ── IDE workspace tool (M2.* — the last Phase-2 slice) ────────────── #
    # If the ADE/IDE subsystem is built, expose its verb set as ONE governed
    # `ide` tool in the SHARED orchestration registry, so a persisted agent run
    # can read/edit/run code inside a workspace. Registered AFTER build_ide +
    # build_orchestration and BEFORE build_agent_engine, so the agent's
    # ToolRouter (which shortlists from this same registry) can offer it. The
    # mutating verbs re-enter the SAME SafetyEngine funnel internally — no new
    # execution path (Constitution).
    if ide.service is not None:
        from atlas.capabilities.ide import (
            IDE_OPERATIONS,
            IDE_TOOL_DESCRIPTION,
            IDEWorkspaceTool,
        )
        from atlas.orchestration.registry import ToolMetadata

        ide_tool = IDEWorkspaceTool(ide.service)
        orch.tool_registry.register(
            ide_tool,
            operations=IDE_OPERATIONS,
            metadata=ToolMetadata(
                name="ide",
                description=IDE_TOOL_DESCRIPTION,
                operations=IDE_OPERATIONS,
                safety_tool="ide",
                side_effects=True,
                idempotent=False,
                supports_rollback=False,
            ),
        )

    # ── Agent-run surface (M2.1) ──────────────────────────────────── #
    # Wire the M0.2 AgentEngine + M0.5 run store into a governed, persisted
    # use-case. Uses the ORCHESTRATION dispatcher/router (funnel-aligned) and the
    # shared event bus + SQLite substrate — NOT the tooling fabric. Off by default
    # (config.agent_engine.enabled): the routes return 503 until it is built.
    from atlas.bootstrap.agent_engine import build_agent_engine

    agent_engine = build_agent_engine(
        settings,
        config,
        gateway=gateway,
        dispatcher=orch.dispatcher,
        tool_router=orch.tool_router,
        events=orch.events,
        ids=ids,
        clock=clock,
        db=db,
        router=router,
    )

    # ── Research surface (Phase 1) ────────────────────────────────── #
    # Wire the governed knowledge pipeline into a persisted, resumable research
    # SESSION surface. Uses the ORCHESTRATION dispatcher (funnel-aligned) — it
    # drives the SAME `knowledge` tool over the SAME knowledge fabric and the SAME
    # SafetyEngine funnel as any other dispatch, adding only session persistence.
    # NOT a second retrieval system or execution path. Off by default
    # (config.research.enabled): the routes return 503 until it is built.
    from atlas.bootstrap.research import build_research

    research = build_research(
        settings,
        config,
        dispatcher=orch.dispatcher,
        ids=ids,
        clock=clock,
        db=db,
        router=router,
    )

    # ── Universal tooling fabric ──────────────────────────────────── #
    # Bridges every real native tool and capability into one registry with
    # adapters that execute through the SAME governed funnels (ToolDispatcher /
    # CapabilityDispatcher -> SafetyEngine). Initialization is isolated: one
    # broken adapter marks its tool FAILED without blocking the rest.
    from atlas.bootstrap.tooling import build_tooling

    tooling = build_tooling(
        tool_registry=orch.tool_registry,
        dispatcher=orch.dispatcher,
        cap_registry=cap_registry,
        cap_dispatcher=cap_dispatcher,
        cap_providers=cap_providers,
        metrics=metrics,
        classifier=classifier,
        db=db,
        bus=bus,
    )
    await tooling.fabric.initialize()

    # ── Routing fabric (Part 3) ───────────────────────────────────── #
    # The control plane over the fabric: TaskIR → domain → strategy →
    # capabilities → catalog candidates → hard policy filter → judgment →
    # ranking → plan/graph. Produces inspectable decisions; NEVER executes
    # (plans flow through the existing governed funnels).
    from atlas.bootstrap.routing import build_routing

    knowledge_available = tooling.registry.get("native:atlas:knowledge") is not None
    routing = build_routing(
        config=config,
        db=db,
        catalog=tooling.catalog,
        knowledge_available=knowledge_available,
        ide_available=config.ide.enabled and ide.service is not None,
        gateway=gateway,
        bus=bus,
    )

    # ── Durable execution fabric (Part 4) ─────────────────────────── #
    # Executes Part-3 RoutePlans: dependency-aware scheduling, bounded
    # parallelism, side-effect-aware retries, fallback/recovery, SQLite
    # checkpoints + resume. Tool steps flow through the SAME governed funnel
    # (ToolingExecutor → dispatchers → SafetyEngine); the Orchestrator stays
    # the task lifecycle owner.
    from atlas.bootstrap.execution import build_execution

    execution = build_execution(
        config=config,
        db=db,
        tooling_executor=tooling.executor,
        catalog=tooling.catalog,
        routing_engine=routing.engine,
        gateway=gateway,
        bus=bus,
    )

    # ── MCP runtime (Part 5) ──────────────────────────────────────── #
    # Official SDK behind an ATLAS boundary: dynamic discovery → normalization
    # → ToolingRegistry + ToolCatalog → routing/execution like every tool.
    from atlas.bootstrap.mcp import build_mcp

    mcp_runtime = build_mcp(
        config_dir=config_dir,
        tooling_registry=tooling.registry,
        catalog=tooling.catalog,
        bus=bus,
        safety=safety,
    )

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
        router=router,
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
        computer_use=computer_use,
        public_api=public_api,
        knowledge_fabric=knowledge_fabric,
        voice_service=voice.service,
        ide_service=ide.service,
        agent_engine=agent_engine.service,
        research=research.service,
        curated=curated,
        lane_one=lane_one,
        intents=intents,
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
        tooling=tooling.fabric,  # Universal tooling fabric
        routing=routing.engine,  # Routing fabric (Part 3)
        execution_engine=execution.engine,  # Durable execution fabric (Part 4)
        mcp_manager=mcp_runtime.manager,  # MCP runtime (Part 5)
        checkpoints=checkpoints,  # Batch 7
        model_registry=intel.registry,  # Model registry for frontend
    )
