"""Notification Platform Builder."""

from __future__ import annotations

from datetime import time
from pathlib import Path

import yaml

from atlas.capabilities.identity.platform import IdentityPlatform
from atlas.capabilities.notification.approval import ApprovalRequestManager
from atlas.capabilities.notification.digest import DigestEngine
from atlas.capabilities.notification.dispatcher import NotificationDispatcher
from atlas.capabilities.notification.domain.models import Channel
from atlas.capabilities.notification.formatter import Formatter
from atlas.capabilities.notification.health import ProviderHealth
from atlas.capabilities.notification.platform import NotificationPlatform
from atlas.capabilities.notification.priority import PriorityEngine
from atlas.capabilities.notification.providers.desktop import DesktopProvider
from atlas.capabilities.notification.providers.ntfy import NtfyProvider
from atlas.capabilities.notification.providers.telegram import TelegramProvider
from atlas.capabilities.notification.queue import NotificationQueue
from atlas.capabilities.notification.quiet_hours import QuietHoursEngine, QuietWindow
from atlas.capabilities.notification.rate_limiter import RateLimiterRegistry
from atlas.capabilities.notification.registry import NotificationRegistry
from atlas.capabilities.notification.resolver import ChannelResolver
from atlas.capabilities.notification.retry import RetryEngine, RetryPolicy
from atlas.capabilities.notification.router import NotificationRouter
from atlas.capabilities.notification.tracker import DeliveryTracker
from atlas.infra.clock import Clock
from atlas.infra.db import Database
from atlas.infra.ids import IdGenerator
from atlas.intelligence.gateway import ModelGateway


def build_notification_platform(
    config_dir: Path, db: Database, clock: Clock, ids: IdGenerator, gateway: ModelGateway,
    identity: IdentityPlatform, callback_base: str
) -> NotificationPlatform:
    # 1. Load config
    cfg_path = config_dir / "notifications.yaml"
    raw = {}
    if cfg_path.exists():
        raw = yaml.safe_load(cfg_path.read_text()) or {}

    # 2. Engines
    registry = NotificationRegistry()
    
    ntfy_pref = raw.get("provider_preferences", {}).get("ntfy", 20)
    registry.register_provider(NtfyProvider(base_url="https://ntfy.sh"), rank=ntfy_pref)
    
    desktop_pref = raw.get("provider_preferences", {}).get("desktop", 30)
    registry.register_provider(DesktopProvider(), rank=desktop_pref)
    
    telegram_pref = raw.get("provider_preferences", {}).get("telegram", 10)
    registry.register_provider(TelegramProvider(identity=identity), rank=telegram_pref)

    for ch_raw in raw.get("channels", []):
        registry.register_channel(Channel(**ch_raw))

    priority = PriorityEngine()
    
    windows = []
    for w in raw.get("quiet_hours", []):
        st = time.fromisoformat(w["start"])
        et = time.fromisoformat(w["end"])
        windows.append(QuietWindow(st, et, w["tz"]))
    quiet = QuietHoursEngine(windows, clock)
    
    resolver = ChannelResolver(registry)
    router = NotificationRouter(priority=priority, quiet=quiet, channels=resolver)
    
    queue = NotificationQueue(db, clock)
    formatter = Formatter()
    health = ProviderHealth()
    limiter = RateLimiterRegistry()
    limiter.register("ntfy", 10, 1.0)
    limiter.register("desktop", 5, 2.0)
    limiter.register("telegram", 5, 1.0)
    
    rcfg = raw.get("retry", {"max_attempts": 3, "base_backoff_s": 1.0, "max_backoff_s": 30.0})
    retry = RetryEngine(RetryPolicy(**rcfg))
    tracker = DeliveryTracker(db)
    
    dispatcher = NotificationDispatcher(
        registry=registry, formatter=formatter, health=health, limiter=limiter,
        retry=retry, tracker=tracker, clock=clock
    )
    
    _digest = DigestEngine(queue=queue, gateway=gateway, ids=ids, clock=clock)
    approvals = ApprovalRequestManager(dispatcher=dispatcher, ids=ids, clock=clock, callback_base=callback_base)
    
    return NotificationPlatform(
        router=router, dispatcher=dispatcher, queue=queue, approvals=approvals
    )
