"""Notification dispatcher — runs the pipeline; the ONLY caller of providers.

ORDER: rate-limit -> health check -> guard() (Safety Engine, capability-gated) ->
provider.send() with retry+failover across ranked channels -> track + audit. A
provider is never called except from here.
"""

from __future__ import annotations

import time
from typing import Any

from atlas.capabilities.notification.domain.models import (
    Channel,
    DeliveryAttempt,
    DeliveryReceipt,
    Notification,
)
from atlas.capabilities.notification.formatter import Formatter
from atlas.capabilities.notification.health import ProviderHealth
from atlas.capabilities.notification.rate_limiter import RateLimiterRegistry
from atlas.capabilities.notification.registry import NotificationRegistry
from atlas.capabilities.notification.retry import RetryEngine
from atlas.capabilities.notification.tracker import DeliveryTracker
from atlas.infra.clock import Clock
from atlas.infra.logging import get_logger

_log = get_logger("atlas.notify.dispatch")


class NotificationDispatcher:
    def __init__(
        self, *, registry: NotificationRegistry, formatter: Formatter,
        health: ProviderHealth, limiter: RateLimiterRegistry, retry: RetryEngine,
        tracker: DeliveryTracker, clock: Clock,
    ) -> None:
        self._registry = registry
        self._formatter = formatter
        self._health = health
        self._limiter = limiter
        self._retry = retry
        self._tracker = tracker
        self._clock = clock

    async def deliver(self, n: Notification, channels: tuple[str, ...],
                      *, multi: bool, retry: bool,
                      actions: tuple[tuple[str, str], ...] = ()) -> DeliveryReceipt:
        rendered = self._formatter.render(n)
        attempts: list[DeliveryAttempt] = []
        ordered = self._registry.rank_channels(channels)
        targets = ordered if multi else ordered[:1]

        for channel_name in targets:
            channel = self._registry.channel(channel_name)
            provider = self._registry.provider_for(channel_name)
            if not channel or not provider or not self._health.is_available(provider.name):
                continue
            ok = await self._attempt(n, channel, provider, rendered, actions, attempts,
                                     allow_retry=retry)
            if ok and not multi:
                break
                
        delivered = any(a.ok for a in attempts)
        receipt = DeliveryReceipt(
            notification_id=n.id, delivered=delivered,
            provider=attempts[-1].provider if attempts else "",
            channel=attempts[-1].channel if attempts else "",
            attempts=tuple(attempts),
            final_error=None if delivered else "all channels failed")
        await self._tracker.record(n, receipt)
        return receipt

    async def _attempt(
        self, n: Notification, channel: Channel, provider: Any, rendered: Any, 
        actions: tuple[tuple[str, str], ...], attempts: list[DeliveryAttempt], *,
        allow_retry: bool
    ) -> bool:
        policy = self._retry.policy(allow_retry)
        attempt = 0
        while True:
            attempt += 1
            self._limiter.take(provider.name)
            start = time.perf_counter()
            try:
                ok = await provider.send(channel, rendered.title, rendered.body, actions=actions)
                latency = int((time.perf_counter() - start) * 1000)
                self._health.record(provider.name, ok=ok, latency_ms=latency)
                attempts.append(DeliveryAttempt(attempt=attempt, provider=provider.name,
                                                channel=channel.name, ts=self._clock.now(),
                                                ok=ok, latency_ms=latency))
                if ok or not self._retry.should_retry(attempt, policy):
                    return ok  # type: ignore
            except Exception as exc:
                latency = int((time.perf_counter() - start) * 1000)
                self._health.record(provider.name, ok=False, latency_ms=latency)
                attempts.append(DeliveryAttempt(attempt=attempt, provider=provider.name,
                                                channel=channel.name, ts=self._clock.now(),
                                                ok=False, latency_ms=latency, error=repr(exc)))
                if not self._retry.should_retry(attempt, policy):
                    return False
            await self._retry.backoff(attempt, policy)
