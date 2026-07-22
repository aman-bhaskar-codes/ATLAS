"""Composition root.

WHY one place: dependency wiring is centralized so the object graph is auditable
in a single file and no module self-constructs its dependencies. WHY the audit
callback: the gateway (infra) must record cost but may not import safety, so we
inject a thin callback that writes to the AuditLog (safety).
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
from atlas.infra.clock import Clock, SystemClock
from atlas.infra.config import AppConfig, Settings, load_app_config, load_permissions, load_settings, resolve_master_key
from atlas.infra.db import Database
from atlas.infra.ids import CorrelationId, IdGenerator, UuidGenerator
from atlas.infra.lifecycle import Lifecycle
from atlas.infra.logging import configure_logging, get_logger
from atlas.infra.metrics import Metrics
from atlas.infra.registry import ServiceRegistry
from atlas.infra.tracing import Tracer
from atlas.infra.types import AuditRecord, Tier
from atlas.intelligence.contracts import Usage
from atlas.intelligence.gateway import ModelGateway
from atlas.intelligence.governance.budget import Budgets
from atlas.intelligence.governance.cost_governor import CostGovernor
from atlas.intelligence.health.health_monitor import HealthMonitor
from atlas.intelligence.observability.telemetry import Telemetry
from atlas.intelligence.providers.ollama import OllamaProvider
from atlas.intelligence.providers.openai_compatible import OpenAICompatibleProvider
from atlas.intelligence.registry.capability_index import CapabilityIndex
from atlas.intelligence.registry.model_registry import ModelRegistry
from atlas.intelligence.registry.provider_registry import ProviderRegistry
from atlas.intelligence.runtime.fallback import FallbackEngine
from atlas.intelligence.runtime.inference import InferenceRuntime
from atlas.intelligence.selection.router import CapabilityRouter
from atlas.intelligence.selection.selector import ModelSelector
from atlas.interfaces.notify import CliConfirmer, CompositeConfirmer
from atlas.memory.consolidation import Consolidator
from atlas.memory.embedder import OllamaEmbedder
from atlas.memory.episodic import EpisodicMemory
from atlas.memory.pruning import Pruner
from atlas.memory.retrieval import Retriever
from atlas.memory.semantic import SemanticMemory
from atlas.memory.user_model import UserModel
from atlas.memory.vectorstore import ChromaVectorStore
from atlas.memory.working import WorkingMemory
from atlas.orchestration.context_builder import ContextBuilder
from atlas.orchestration.dispatcher import ToolDispatcher
from atlas.orchestration.events import EventPublisher
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
from atlas.orchestration.router import Router
from atlas.orchestration.self_critique import SelfCritique
from atlas.orchestration.tiering import TierEstimator
from atlas.orchestration.types import Action, Critique
from atlas.orchestration.validator import OutputValidator
from atlas.safety.audit import AuditLog
from atlas.safety.classifier import TierClassifier
from atlas.safety.engine import SafetyEngine
from atlas.safety.killswitch import KillSwitch
from atlas.safety.manifest import Manifest, load_manifest
from atlas.safety.policy import KillSwitchPolicy, PolicyEngine
from atlas.safety.sandbox_docker import DockerSandbox, SandboxSpec
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
    episodic: EpisodicMemory
    semantic: SemanticMemory
    user_model: UserModel
    working: WorkingMemory
    retriever: Retriever
    consolidator: Consolidator
    pruner: Pruner
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

    async def start(self) -> None:
        await self.db.start()
        await self.lifecycle.start()

    async def close(self) -> None:
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

    ids: IdGenerator = UuidGenerator()
    clock: Clock = SystemClock()
    metrics = Metrics()
    tracer = Tracer(config.tracing)

    db = Database(settings.db_path())
    registry = ServiceRegistry()
    registry.register("db", db)
    lifecycle = Lifecycle(registry)
    
    bus = MessageBus()

    audit = AuditLog(db)
    killswitch = KillSwitch(config.safety.stop_flag_path)
    classifier = TierClassifier(manifest, config.safety.default_tier_on_error)
    policy = PolicyEngine((KillSwitchPolicy(killswitch),))
    safety = SafetyEngine(
        classifier=classifier, policy=policy, audit=audit,
        killswitch=killswitch, clock=clock, cfg=config.safety,
    )

    async def cap_audit(**kw: Any) -> None:
        await audit.record(AuditRecord(
            correlation_id=CorrelationId(kw["correlation_id"]), ts=clock.now(), actor=kw["actor"],
            action=kw["action"], tool=kw.get("tool"), outcome=kw.get("outcome"),
            payload=kw.get("payload")))

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

    async def on_audit_cost(corr: str, provider: str, model_id: str, usage: Usage, latency_ms: int) -> None:
        await audit.record(AuditRecord(
            correlation_id=CorrelationId(corr), ts=clock.now(), actor="intel_platform",
            action="model.call", outcome="ok",
            cost_tokens=usage.input_tokens + usage.output_tokens,
            cost_usd=usage.usd,
            payload={"model": model_id, "provider": provider, "latency_ms": latency_ms},
        ))

    telemetry = Telemetry(on_audit_cost)
    health = HealthMonitor()
    
    budgets = Budgets(
        daily_usd=config.models.daily_usd,
        weekly_usd=config.models.weekly_usd,
        monthly_usd=config.models.monthly_usd,
        per_task_usd=config.models.per_task_usd,
    )
    governor = CostGovernor(spend=audit, budgets=budgets)

    provider_registry = ProviderRegistry()
    provider_registry.register(OllamaProvider(settings.ollama_host, config.models.local_timeout_s))
    if config.models.allow_cloud:
        if settings.deepseek_api_key:
            provider_registry.register(OpenAICompatibleProvider(
                name="deepseek", base_url="https://api.deepseek.com",
                api_key=settings.deepseek_api_key, timeout_s=config.models.cloud_timeout_s
            ))
        if settings.glm_api_key:
            provider_registry.register(OpenAICompatibleProvider(
                name="glm", base_url="https://open.bigmodel.cn/api/paas/v4",
                api_key=settings.glm_api_key, timeout_s=config.models.cloud_timeout_s
            ))
        if settings.kimi_api_key:
            provider_registry.register(OpenAICompatibleProvider(
                name="kimi", base_url="https://api.moonshot.cn/v1",
                api_key=settings.kimi_api_key, timeout_s=config.models.cloud_timeout_s
            ))

    model_registry = ModelRegistry.from_yaml(config_dir / "models.yaml")
    capability_index = CapabilityIndex(model_registry)
    
    runtime = InferenceRuntime(
        providers=provider_registry, health=health,
        governor=governor, telemetry=telemetry,
        model_timeout_s=config.models.cloud_timeout_s,
    )
    fallback = FallbackEngine()
    cap_router = CapabilityRouter()
    selector = ModelSelector(capability_index, health)
    
    gateway = ModelGateway(
        router=cap_router, selector=selector,
        fallback=fallback, runtime=runtime,
    )

    cap_registry = CapabilityRegistry()

    notification_platform = build_notification_platform(
        config_dir=config_dir, db=db, clock=clock, ids=ids, gateway=gateway,
        identity=identity_platform, callback_base=settings.ntfy_callback_base
    )

    safety.set_confirmer(
        CompositeConfirmer(notification_platform, CliConfirmer(), config.notify.confirm_timeout_s)  # type: ignore
    )
    cap_health = CapabilityHealth()
    cap_providers = CapProviderRegistry(cap_health)
    ext_cap_router = ExtCapabilityRouter(gateway)

    cap_telemetry = CapabilityTelemetry(cap_audit)

    cap_dispatcher = CapabilityDispatcher(
        registry=cap_registry, providers=cap_providers, health=cap_health,
        safety=safety, telemetry=cap_telemetry)

    # Phase 2 Tools
    sandbox = DockerSandbox(SandboxSpec(
        image=config.sandbox.image, cpus=config.sandbox.cpus,
        memory=config.sandbox.memory, pids_limit=config.sandbox.pids_limit,
        workdir=config.sandbox.workdir
    ))
    
    # We mount the workspace explicitly for the shell tool in Phase 2
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
            mounts={ws: "/work"}
        ),
    }

    vectors = ChromaVectorStore(str(settings.data_dir / "chroma"))
    embedder = OllamaEmbedder(settings)
    episodic = EpisodicMemory(db, clock)
    semantic = SemanticMemory(db, vectors, embedder, ids, clock)
    user_model = UserModel(db, clock)
    working = WorkingMemory()
    retriever = Retriever(semantic=semantic, episodic=episodic, user_model=user_model)
    consolidator = Consolidator(episodic=episodic, semantic=semantic, gateway=gateway,
                                db=db, ids=ids, clock=clock)
    pruner = Pruner(db=db, gateway=gateway, ids=ids, clock=clock)

    # Phase 6.3 Knowledge Platform
    cap_registry.register(CapabilitySpec(
        capability=Capability.KNOWLEDGE, safety_tool="knowledge",
        operations=("search",), default_tier=Tier.AUTO, requires_auth=False,
        description="Obtain knowledge from memory + official + web sources"))

    try:
        ksrc = yaml.safe_load((config_dir / "knowledge_sources.yaml").read_text())
    except Exception:
        ksrc = {"official_feeds": {}, "provider_preferences": {}}
        
    official: list[KnowledgeProvider] = [RSSProvider(name=k, feeds=v) for k, v in ksrc.get("official_feeds", {}).items()]
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

    # Phase 6.5 Email Platform
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

    # Phase 6.6 Calendar & Contacts Platform
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
                   "default_calendar": "primary",
                   "commit": {"approval_channels": []}}
    try:
        con_cfg: dict[str, Any] = yaml.safe_load((config_dir / "contacts.yaml").read_text())
    except Exception:
        con_cfg = {"accounts": [{"credential_id": "google:anti@gmail.com"}],
                   "known_contacts": {"sync_on_start": False, "seed": []}}

    people = GooglePeopleProvider(
        identity_platform,
        credential_id=con_cfg["accounts"][0]["credential_id"])
    approval_channels = tuple(cal_cfg.get("commit", {}).get("approval_channels", []))
    contacts_platform = ContactsPlatform(
        provider=people, notifications=notification_platform, ids=ids,
        approval_channels=approval_channels,
        seed=set(con_cfg.get("known_contacts", {}).get("seed", [])))

    # Sync known contacts if configured; otherwise start with seed only
    kc_cfg = con_cfg.get("known_contacts", {})
    if kc_cfg.get("sync_on_start", False):
        known = await contacts_platform.sync_known()
    else:
        known = KnownContacts(set(kc_cfg.get("seed", [])))

    # Feed the SAME KnownContacts into the email platform (replaces 6.5 local set)
    email_platform.set_known_contacts(known)

    gcal = GoogleCalendarProvider(
        identity_platform,
        credential_id=cal_cfg["accounts"][0]["credential_id"])
    calendar_platform = CalendarPlatform(
        provider=gcal, notifications=notification_platform, ids=ids, known=known,
        approval_channels=approval_channels,
        default_calendar=cal_cfg.get("default_calendar", "primary"))

    # Phase 6.7 — Browser Platform (optional; only built when enabled in config)
    browser_platform: BrowserPlatform | None = None
    if config.browser.enabled:
        browser_platform = build_browser_platform(
            ids=ids,
            notifications=notification_platform,
            approval_channels=tuple(approval_channels),
        )

    tool_registry = ToolRegistry()
    for t in tools.values():
        tool_registry.register(t, ("read", "write", "delete", "side_effect", "read_only"))


    events = EventPublisher(bus)
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
        )
    else:
        reflection = NoOpReflection()

    dispatcher = ToolDispatcher(tool_registry, safety)
    limits = ExecutionLimits(max_steps=15)
    
    reasoning = ReasoningLoop(
        gateway=gateway, dispatcher=dispatcher, parser=parser,
        validator=validator, prompts=prompts, recorder=recorder,
        monitor=monitor, retry=retry, reflection=reflection,
        events=events, limits=limits,
    )
    
    orchestrator = Orchestrator(
        ids=ids, clock=clock, router=router, planner=planner,
        context_builder=context_builder, reasoning=reasoning,
        registry=tool_registry, events=events,
    )

    _log.info("core.ready", event_type="lifecycle", providers=provider_registry.names())
    return Atlas(
        settings=settings, config=config, manifest=manifest, db=db, registry=registry,
        lifecycle=lifecycle, ids=ids, clock=clock, metrics=metrics, tracer=tracer,
        audit=audit, killswitch=killswitch, classifier=classifier, safety=safety,
        tools=tools, gateway=gateway, notification_platform=notification_platform,
        vectors=vectors, embedder=embedder, episodic=episodic, semantic=semantic,
        user_model=user_model, working=working, retriever=retriever,
        consolidator=consolidator, pruner=pruner, bus=bus, orchestrator=orchestrator,
        cap_registry=cap_registry, cap_health=cap_health, cap_providers=cap_providers,
        ext_cap_router=ext_cap_router,
        cap_dispatcher=cap_dispatcher,
        cap_telemetry=cap_telemetry,
        identity=identity_platform,
        knowledge_platform=knowledge_platform,
        email_platform=email_platform,
        calendar_platform=calendar_platform,
        contacts_platform=contacts_platform,
        browser_platform=browser_platform,
    )
