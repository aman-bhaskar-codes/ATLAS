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

import yaml

from atlas.capabilities.browser.builder import build_browser_platform
from atlas.capabilities.browser.platform import BrowserPlatform
from atlas.capabilities.dispatcher import CapabilityDispatcher
from atlas.capabilities.identity.auth.api_key import ApiKeyStrategy
from atlas.capabilities.identity.auth.browser_session import BrowserSessionStrategy
from atlas.capabilities.identity.auth.jwt import JwtStrategy
from atlas.capabilities.identity.models import CredentialKind
from atlas.capabilities.identity.platform import IdentityPlatform
from atlas.capabilities.identity.secret_store import SecretStore
from atlas.capabilities.notification.builder import build_notification_platform
from atlas.capabilities.notification.platform import NotificationPlatform
from atlas.capabilities.observability.telemetry import CapabilityTelemetry
from atlas.capabilities.platforms.calendar_platform import CalendarPlatform
from atlas.capabilities.platforms.contacts_platform import ContactsPlatform
from atlas.capabilities.platforms.email_platform import EmailPlatform
from atlas.capabilities.platforms.knowledge_platform import KnowledgePlatform
from atlas.capabilities.platforms.knowledge_router import KnowledgeRouter as KnowRouter
from atlas.capabilities.providers.knowledge.arxiv import ArxivProvider
from atlas.capabilities.providers.knowledge.base import KnowledgeProvider
from atlas.capabilities.providers.knowledge.brave import BraveSearchProvider
from atlas.capabilities.providers.knowledge.duckduckgo import DuckDuckGoProvider
from atlas.capabilities.providers.knowledge.github_releases import GitHubReleasesProvider
from atlas.capabilities.providers.knowledge.memory_source import MemoryKnowledgeSource
from atlas.capabilities.providers.knowledge.parametric import ParametricKnowledgeSource
from atlas.capabilities.providers.knowledge.rss import RSSProvider
from atlas.capabilities.providers.knowledge.tavily import TavilySearchProvider
from atlas.capabilities.providers.knowledge.wikipedia import WikipediaProvider
from atlas.capabilities.registry.capability import Capability, CapabilityRegistry, CapabilitySpec
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
from atlas.infra.types import Tier
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
    trajectory_store: Any  # Phase 2: TrajectoryStore
    experience_extractor: Any  # Phase 2: ExperienceExtractor
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
    browser_platform: BrowserPlatform | None = None
    feedback: FeedbackStore | None = None
    scheduler: CronScheduler | None = None
    llm_tracker: LLMCallTracker | None = None
    workflows: WorkflowStore | None = None

    async def start(self) -> None:
        # lifecycle.start() calls db.start() and bus.start() via the service registry
        await self.lifecycle.start()
        # Phase 0: Connect memory subsystems to event bus in ALL execution paths (not just API)
        self.episodic.set_bus(self.bus)
        self.semantic.set_bus(self.bus)
        self.user_model.set_bus(self.bus)
        self.knowledge_store.set_bus(self.bus)
        await self.bus.start()
        await self.embedding_worker.start()

    async def close(self) -> None:
        await self.embedding_worker.stop()
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
        config=config, manifest=manifest, audit=audit,
        killswitch=killswitch, clock=clock, ids=ids,
    )
    classifier, safety, cap_audit = saf.classifier, saf.safety, saf.cap_audit

    # ── Identity ─────────────────────────────────────────────────── #
    master_key = resolve_master_key(settings)
    secret_store = SecretStore(db, master_key)
    identity_platform = IdentityPlatform(
        store=secret_store, db=db,
        strategies={
            CredentialKind.API_KEY: ApiKeyStrategy(),
            CredentialKind.JWT: JwtStrategy(),
            CredentialKind.BROWSER_SESSION: BrowserSessionStrategy(),
        },
        audit=cap_audit,
    )

    # ── Intelligence ──────────────────────────────────────────────── #
    from atlas.bootstrap.intelligence import build_intelligence
    intel = await build_intelligence(
        settings=settings, config=config, config_dir=config_dir,
        db=db, ids=ids, clock=clock, audit=audit,
    )
    gateway, embedder, llm_tracker = intel.gateway, intel.embedder, intel.llm_tracker

    # ── Notifications ─────────────────────────────────────────────── #
    class NotificationPlatformAdapter:
        def __init__(self, platform: Any, clock: Clock, ids: IdGenerator) -> None:
            self._platform = platform
            self._clock = clock
            self._ids = ids

        async def notify(self, title: str, body: str, *, priority: int = 3) -> None:
            from atlas.capabilities.notification.domain.models import (
                Notification, NotificationKind, NotificationPriority,
            )
            p = NotificationPriority(priority) if priority in (0, 1, 2, 3) else NotificationPriority.NORMAL
            n = Notification(
                id=self._ids.execution_id(), correlation_id=self._ids.correlation_id(),
                kind=NotificationKind.WARNING, priority=p,
                title=title, body=body, urgent=True, created_ts=self._clock.now()
            )
            await self._platform.notify(n)

        async def ask(self, title: str, body: str, *, timeout_s: float) -> bool | None:
            from atlas.capabilities.notification.domain.models import ApprovalRequest
            req = ApprovalRequest(
                id=self._ids.execution_id(), correlation_id=self._ids.correlation_id(),
                prompt=title, detail=body, timeout_s=timeout_s, default_on_timeout=False
            )
            decision = await self._platform.request_approval(req, channels=())
            if decision.timed_out:
                return None
            return bool(decision.approved)

    notification_platform = build_notification_platform(
        config_dir=config_dir, db=db, clock=clock, ids=ids, gateway=gateway,
        identity=identity_platform, callback_base=settings.ntfy_callback_base
    )
    notifier_adapter = NotificationPlatformAdapter(notification_platform, clock, ids)
    active_notifier = notifier_adapter if settings.ntfy_topic else None
    safety.set_confirmer(
        CompositeConfirmer(active_notifier, CliConfirmer(), config.notify.confirm_timeout_s)
    )

    # ── Capability infrastructure ─────────────────────────────────── #
    cap_registry = CapabilityRegistry()
    cap_health = CapabilityHealth()
    cap_providers = CapProviderRegistry(cap_health)
    ext_cap_router = ExtCapabilityRouter(gateway)
    cap_telemetry = CapabilityTelemetry(cap_audit)
    cap_dispatcher = CapabilityDispatcher(
        registry=cap_registry, providers=cap_providers, health=cap_health,
        safety=safety, telemetry=cap_telemetry,
    )

    # ── Sandboxed tools ───────────────────────────────────────────── #
    docker_sandbox = DockerSandbox(SandboxSpec(
        image=config.sandbox.image, cpus=config.sandbox.cpus,
        memory=config.sandbox.memory, pids_limit=config.sandbox.pids_limit,
        workdir=config.sandbox.workdir,
    ))
    _docker_ok = await docker_sandbox.health()
    if _docker_ok:
        sandbox = docker_sandbox
        _log.info("sandbox.docker", event_type="lifecycle", detail="Docker available")
    elif settings.env == "dev":
        sandbox = NativeSandbox(env=settings.env)  # type: ignore[assignment]
        _log.warning("sandbox.native", event_type="lifecycle",
                     detail="Docker unavailable — using native sandbox (dev only)")
    else:
        sandbox = docker_sandbox
        _log.error("sandbox.docker_required", event_type="lifecycle",
                   detail="Docker is required in non-dev environments")

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
            sandbox=sandbox, mounts={ws: "/work"},
        ),
    }

    # ── Memory ────────────────────────────────────────────────────── #
    from atlas.bootstrap.memory import build_memory
    mem = build_memory(
        settings=settings, db=db, ids=ids, clock=clock, embedder=embedder, gateway=gateway,
    )
    vectors = mem.vectors
    embedding_worker = mem.embedding_worker
    episodic, semantic = mem.episodic, mem.semantic
    user_model, working = mem.user_model, mem.working
    knowledge_store = mem.knowledge_store
    retriever, consolidator, pruner = mem.retriever, mem.consolidator, mem.pruner
    trajectory_store, experience_extractor = mem.trajectory_store, mem.experience_extractor  # Phase 2

    # ── Knowledge platform providers ──────────────────────────────── #
    cap_registry.register(CapabilitySpec(
        capability=Capability.KNOWLEDGE, safety_tool="knowledge",
        operations=("search",), default_tier=Tier.AUTO, requires_auth=False,
        description="Obtain knowledge from memory + official + web sources"))

    try:
        ksrc = yaml.safe_load((config_dir / "knowledge_sources.yaml").read_text())
    except Exception:
        ksrc = {"official_feeds": {}, "provider_preferences": {}}

    official: list[KnowledgeProvider] = [
        RSSProvider(name=k, feeds=v) for k, v in ksrc.get("official_feeds", {}).items()
    ]
    official += [WikipediaProvider(), ArxivProvider(), GitHubReleasesProvider()]
    web: list[KnowledgeProvider] = [DuckDuckGoProvider()]
    if config.models.allow_cloud:
        try:
            web.append(BraveSearchProvider(identity_platform, credential_id="brave:default"))
        except Exception:
            pass
        try:
            web.append(TavilySearchProvider(identity_platform, credential_id="tavily:default"))
        except Exception:
            pass

    memory_source = MemoryKnowledgeSource(retriever)
    parametric = ParametricKnowledgeSource(gateway)

    prefs = ksrc.get("provider_preferences", {})
    def _pref(p_dict: dict[str, int], name: str) -> int:
        if name in p_dict:
            return p_dict[name]
        for k, v in p_dict.items():
            if k.endswith("*") and name.startswith(k[:-1]):
                return v
        return 100

    for p in [*official, *web]:
        cap_providers.register(p, preference=_pref(prefs, p.name))

    knowledge_router = KnowRouter(gateway)
    knowledge_platform = KnowledgePlatform(
        router=knowledge_router, gateway=gateway, episodic=episodic, ids=ids, clock=clock,
        official=official, web=web, memory_source=memory_source, parametric=parametric)

    # ── Email platform ────────────────────────────────────────────── #
    from atlas.capabilities.platforms.email_platform import EmailPlatform
    from atlas.capabilities.providers.email.gmail import GmailProvider

    cap_registry.register(CapabilitySpec(
        capability=Capability.EMAIL, safety_tool="email",
        operations=("read", "search", "compose", "send"),
        default_tier=Tier.NOTIFY, requires_auth=True,
        description="Read/search/compose/send email; send is Tier-2 previewed"))

    try:
        email_cfg: dict[str, Any] = yaml.safe_load((config_dir / "email.yaml").read_text())
    except Exception:
        email_cfg = {"accounts": [{"credential_id": "google:anti@gmail.com"}], "send": {"approval_channels": []}}

    gmail = GmailProvider(identity_platform, credential_id=email_cfg.get("accounts", [{}])[0].get("credential_id", ""))
    email_platform = EmailPlatform(
        provider=gmail, notifications=notification_platform, ids=ids,
        known_contacts=set(email_cfg.get("known_contacts", [])),
        approval_channels=tuple(email_cfg.get("send", {}).get("approval_channels", [])))

    # ── Calendar & Contacts ───────────────────────────────────────── #
    from atlas.capabilities.domain.contacts import KnownContacts
    from atlas.capabilities.providers.calendar.google_calendar import GoogleCalendarProvider
    from atlas.capabilities.providers.contacts.google_people import GooglePeopleProvider

    cap_registry.register(CapabilitySpec(
        capability=Capability.CONTACTS, safety_tool="contacts",
        operations=("read", "search", "create", "update"),
        default_tier=Tier.NOTIFY, requires_auth=True,
        description="Read/search/create/update contacts; writes Tier-2 previewed"))
    cap_registry.register(CapabilitySpec(
        capability=Capability.CALENDAR, safety_tool="calendar",
        operations=("read", "search", "freebusy", "compose", "create", "update", "delete"),
        default_tier=Tier.NOTIFY, requires_auth=True,
        description="Read/search/free-busy + create/update/delete; writes Tier-2 previewed"))

    try:
        cal_cfg: dict[str, Any] = yaml.safe_load((config_dir / "calendar.yaml").read_text())
    except Exception:
        cal_cfg = {"accounts": [{"credential_id": "google:anti@gmail.com"}],
                   "default_calendar": "primary", "commit": {"approval_channels": []}}
    try:
        con_cfg: dict[str, Any] = yaml.safe_load((config_dir / "contacts.yaml").read_text())
    except Exception:
        con_cfg = {"accounts": [{"credential_id": "google:anti@gmail.com"}],
                   "known_contacts": {"sync_on_start": False, "seed": []}}

    people = GooglePeopleProvider(
        identity_platform, credential_id=con_cfg["accounts"][0]["credential_id"])
    approval_channels = tuple(cal_cfg.get("commit", {}).get("approval_channels", []))
    contacts_platform = ContactsPlatform(
        provider=people, notifications=notification_platform, ids=ids,
        approval_channels=approval_channels,
        seed=set(con_cfg.get("known_contacts", {}).get("seed", [])))

    kc_cfg = con_cfg.get("known_contacts", {})
    if kc_cfg.get("sync_on_start", False):
        known = await contacts_platform.sync_known()
    else:
        known = KnownContacts(set(kc_cfg.get("seed", [])))

    email_platform.set_known_contacts(known)

    gcal = GoogleCalendarProvider(
        identity_platform, credential_id=cal_cfg["accounts"][0]["credential_id"])
    calendar_platform = CalendarPlatform(
        provider=gcal, notifications=notification_platform, ids=ids, known=known,
        approval_channels=approval_channels,
        default_calendar=cal_cfg.get("default_calendar", "primary"))

    # ── Browser platform (optional) ───────────────────────────────── #
    browser_platform: BrowserPlatform | None = None
    if config.browser.enabled:
        browser_platform = build_browser_platform(
            ids=ids, notifications=notification_platform,
            approval_channels=tuple(approval_channels),
        )

    # ── Orchestration ─────────────────────────────────────────────── #
    from atlas.bootstrap.orchestration import build_orchestration
    orch = build_orchestration(
        config=config, ids=ids, clock=clock, db=db, audit=audit,
        classifier=classifier, killswitch=killswitch, safety=safety,
        gateway=gateway, retriever=retriever, working=working, semantic=semantic,
        bus=bus, tools=tools, episodic=episodic,
        trajectory_store=trajectory_store,  # Phase 2
        experience_extractor=experience_extractor,  # Phase 2
    )
    orchestrator = orch.orchestrator

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
            _log.error("consolidation.scheduled_error", event_type="lifecycle", error=str(exc))

    cron_scheduler.register_job(name="memory_consolidation", cron="0 2 * * *", fn=_consolidate_job)

    _log.info("core.ready", event_type="lifecycle", providers=str(type(gateway)))
    return Atlas(
        settings=settings, config=config, manifest=manifest, db=db, registry=registry,
        lifecycle=lifecycle, ids=ids, clock=clock, metrics=metrics, tracer=tracer,
        audit=audit, killswitch=killswitch, classifier=classifier, safety=safety,
        tools=tools, gateway=gateway, notification_platform=notification_platform,
        vectors=vectors, embedder=embedder, embedding_worker=embedding_worker,
        episodic=episodic, semantic=semantic,
        user_model=user_model, working=working, retriever=retriever,
        consolidator=consolidator, pruner=pruner, knowledge_store=knowledge_store,
        trajectory_store=trajectory_store, experience_extractor=experience_extractor,  # Phase 2
        bus=bus, orchestrator=orchestrator,
        cap_registry=cap_registry, cap_health=cap_health, cap_providers=cap_providers,
        ext_cap_router=ext_cap_router, cap_dispatcher=cap_dispatcher,
        cap_telemetry=cap_telemetry, identity=identity_platform,
        knowledge_platform=knowledge_platform, email_platform=email_platform,
        calendar_platform=calendar_platform, contacts_platform=contacts_platform,
        browser_platform=browser_platform,
        feedback=feedback_store, scheduler=cron_scheduler,
        llm_tracker=llm_tracker, workflows=workflow_store,
    )

