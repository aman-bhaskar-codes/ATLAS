"""Capabilities bootstrap — identity, knowledge, email, calendar, contacts platforms.

WHY: Consolidate capability platform construction that was previously inline in
app.py into builder functions. Mirrors build_intelligence() pattern.
Browser and notification platforms already have their own builders.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from atlas.capabilities.domain.contacts import KnownContacts
from atlas.capabilities.identity.auth.api_key import ApiKeyStrategy
from atlas.capabilities.identity.auth.browser_session import BrowserSessionStrategy
from atlas.capabilities.identity.auth.jwt import JwtStrategy
from atlas.capabilities.identity.models import CredentialKind
from atlas.capabilities.identity.platform import IdentityPlatform
from atlas.capabilities.identity.secret_store import SecretStore
from atlas.capabilities.notification.platform import NotificationPlatform
from atlas.capabilities.platforms.calendar_platform import CalendarPlatform
from atlas.capabilities.platforms.contacts_platform import ContactsPlatform
from atlas.capabilities.platforms.currency_platform import CurrencyPlatform
from atlas.capabilities.platforms.email_platform import EmailPlatform
from atlas.capabilities.platforms.knowledge_platform import KnowledgePlatform
from atlas.capabilities.platforms.knowledge_router import KnowledgeRouter
from atlas.capabilities.platforms.location_platform import LocationPlatform
from atlas.capabilities.platforms.weather_platform import WeatherPlatform
from atlas.capabilities.providers.calendar.google_calendar import GoogleCalendarProvider
from atlas.capabilities.providers.contacts.google_people import GooglePeopleProvider
from atlas.capabilities.providers.currency.frankfurter import FrankfurterProvider
from atlas.capabilities.providers.email.gmail import GmailProvider
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
from atlas.capabilities.providers.location.nominatim import NominatimProvider
from atlas.capabilities.providers.weather.open_meteo import OpenMeteoProvider
from atlas.capabilities.registry.capability import Capability, CapabilityRegistry, CapabilitySpec
from atlas.capabilities.registry.provider_registry import ProviderRegistry as CapProviderRegistry
from atlas.infra.clock import Clock
from atlas.infra.config import AppConfig
from atlas.infra.db import Database
from atlas.infra.ids import IdGenerator
from atlas.infra.logging import get_logger
from atlas.infra.types import Tier
from atlas.intelligence.gateway import ModelGateway
from atlas.memory.episodic import EpisodicMemory
from atlas.memory.retrieval import Retriever

_log = get_logger("atlas.bootstrap.capabilities")


def build_identity_platform(
    *,
    db: Database,
    cap_audit: Any,
    master_key: str,
) -> IdentityPlatform:
    """Build identity platform with credential store and auth strategies."""
    secret_store = SecretStore(db, master_key)
    identity_platform = IdentityPlatform(
        store=secret_store,
        db=db,
        strategies={
            CredentialKind.API_KEY: ApiKeyStrategy(),
            CredentialKind.JWT: JwtStrategy(),
            CredentialKind.BROWSER_SESSION: BrowserSessionStrategy(),
        },
        audit=cap_audit,
    )
    _log.info("identity.ready", event_type="lifecycle")
    return identity_platform


@dataclass
class DataPlatformsComponents:
    knowledge: KnowledgePlatform
    email: EmailPlatform
    calendar: CalendarPlatform
    contacts: ContactsPlatform
    known_contacts: KnownContacts
    weather_platform: WeatherPlatform
    location_platform: LocationPlatform
    currency_platform: CurrencyPlatform


async def build_data_platforms(
    *,
    config: AppConfig,
    config_dir: Path,
    db: Database,
    ids: IdGenerator,
    clock: Clock,
    gateway: ModelGateway,
    retriever: Retriever,
    episodic: EpisodicMemory,
    identity: IdentityPlatform,
    notification_platform: NotificationPlatform,
    cap_registry: CapabilityRegistry,
    cap_providers: CapProviderRegistry,
) -> DataPlatformsComponents:
    """Build data capability platforms: knowledge, email, calendar, contacts.

    Extracted from app.py to follow bootstrap pattern. Requires identity and
    notification platforms as dependencies.
    """

    # ── Knowledge Platform ────────────────────────────────────────────
    cap_registry.register(
        CapabilitySpec(
            capability=Capability.KNOWLEDGE,
            safety_tool="knowledge",
            operations=("search",),
            default_tier=Tier.AUTO,
            requires_auth=False,
            description="Obtain knowledge from memory + official + web sources",
        )
    )

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
            web.append(BraveSearchProvider(identity, credential_id="brave:default"))
        except Exception:
            pass
        try:
            web.append(TavilySearchProvider(identity, credential_id="tavily:default"))
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

    knowledge_router = KnowledgeRouter(gateway)
    knowledge_platform = KnowledgePlatform(
        router=knowledge_router,
        gateway=gateway,
        episodic=episodic,
        ids=ids,
        clock=clock,
        official=official,
        web=web,
        memory_source=memory_source,
        parametric=parametric,
    )
    _log.info("knowledge.ready", event_type="lifecycle", official_count=len(official), web_count=len(web))

    # ── Weather Platform ───────────────────────────────────────────────
    cap_registry.register(
        CapabilitySpec(
            capability=Capability.WEATHER,
            safety_tool="weather",
            operations=("forecast",),
            default_tier=Tier.AUTO,
            requires_auth=False,
            description="Get weather forecast for a location",
        )
    )

    weather_platform = WeatherPlatform(provider=OpenMeteoProvider())
    _log.info("weather.ready", event_type="lifecycle")

    # ── Location Platform ──────────────────────────────────────────────
    cap_registry.register(
        CapabilitySpec(
            capability=Capability.LOCATION,
            safety_tool="location",
            operations=("geocode", "country_info"),
            default_tier=Tier.AUTO,
            requires_auth=False,
            description="Geocode addresses and look up country metadata",
        )
    )

    location_platform = LocationPlatform(provider=NominatimProvider())
    _log.info("location.ready", event_type="lifecycle")

    # ── Currency Platform ──────────────────────────────────────────────
    cap_registry.register(
        CapabilitySpec(
            capability=Capability.CURRENCY,
            safety_tool="currency",
            operations=("convert",),
            default_tier=Tier.AUTO,
            requires_auth=False,
            description="Convert between currencies using live exchange rates",
        )
    )

    currency_platform = CurrencyPlatform(provider=FrankfurterProvider())
    _log.info("currency.ready", event_type="lifecycle")

    # ── Email Platform ────────────────────────────────────────────────
    cap_registry.register(
        CapabilitySpec(
            capability=Capability.EMAIL,
            safety_tool="email",
            operations=("read", "search", "compose", "send"),
            default_tier=Tier.NOTIFY,
            requires_auth=True,
            description="Read/search/compose/send email; send is Tier-2 previewed",
        )
    )

    try:
        email_cfg: dict[str, Any] = yaml.safe_load((config_dir / "email.yaml").read_text())
    except Exception:
        email_cfg = {"accounts": [{"credential_id": "google:anti@gmail.com"}], "send": {"approval_channels": []}}

    gmail = GmailProvider(identity, credential_id=email_cfg.get("accounts", [{}])[0].get("credential_id", ""))
    email_platform = EmailPlatform(
        provider=gmail,
        notifications=notification_platform,
        ids=ids,
        known_contacts=set(email_cfg.get("known_contacts", [])),
        approval_channels=tuple(email_cfg.get("send", {}).get("approval_channels", [])),
    )
    _log.info("email.ready", event_type="lifecycle")

    # ── Calendar & Contacts ───────────────────────────────────────────
    cap_registry.register(
        CapabilitySpec(
            capability=Capability.CONTACTS,
            safety_tool="contacts",
            operations=("read", "search", "create", "update"),
            default_tier=Tier.NOTIFY,
            requires_auth=True,
            description="Read/search/create/update contacts; writes Tier-2 previewed",
        )
    )
    cap_registry.register(
        CapabilitySpec(
            capability=Capability.CALENDAR,
            safety_tool="calendar",
            operations=("read", "search", "freebusy", "compose", "create", "update", "delete"),
            default_tier=Tier.NOTIFY,
            requires_auth=True,
            description="Read/search/free-busy + create/update/delete; writes Tier-2 previewed",
        )
    )

    try:
        cal_cfg: dict[str, Any] = yaml.safe_load((config_dir / "calendar.yaml").read_text())
    except Exception:
        cal_cfg = {
            "accounts": [{"credential_id": "google:anti@gmail.com"}],
            "default_calendar": "primary",
            "commit": {"approval_channels": []},
        }
    try:
        con_cfg: dict[str, Any] = yaml.safe_load((config_dir / "contacts.yaml").read_text())
    except Exception:
        con_cfg = {
            "accounts": [{"credential_id": "google:anti@gmail.com"}],
            "known_contacts": {"sync_on_start": False, "seed": []},
        }

    people = GooglePeopleProvider(identity, credential_id=con_cfg["accounts"][0]["credential_id"])
    approval_channels = tuple(cal_cfg.get("commit", {}).get("approval_channels", []))
    contacts_platform = ContactsPlatform(
        provider=people,
        notifications=notification_platform,
        ids=ids,
        approval_channels=approval_channels,
        seed=set(con_cfg.get("known_contacts", {}).get("seed", [])),
    )

    kc_cfg = con_cfg.get("known_contacts", {})
    if kc_cfg.get("sync_on_start", False):
        known = await contacts_platform.sync_known()
    else:
        known = KnownContacts(set(kc_cfg.get("seed", [])))

    email_platform.set_known_contacts(known)

    gcal = GoogleCalendarProvider(identity, credential_id=cal_cfg["accounts"][0]["credential_id"])
    calendar_platform = CalendarPlatform(
        provider=gcal,
        notifications=notification_platform,
        ids=ids,
        known=known,
        approval_channels=approval_channels,
        default_calendar=cal_cfg.get("default_calendar", "primary"),
    )
    _log.info("calendar.ready", event_type="lifecycle")
    _log.info("contacts.ready", event_type="lifecycle")

    return DataPlatformsComponents(
        knowledge=knowledge_platform,
        email=email_platform,
        calendar=calendar_platform,
        contacts=contacts_platform,
        known_contacts=known,
        weather_platform=weather_platform,
        location_platform=location_platform,
        currency_platform=currency_platform,
    )
