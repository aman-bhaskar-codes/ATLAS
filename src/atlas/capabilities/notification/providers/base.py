"""NotificationProvider — the one contract every channel backend implements.

WHY mirror the 6.1 Provider shape: consistency. A provider does init/health/send/
shutdown and normalizes nothing beyond a DeliveryReceipt. It NEVER decides routing,
quiet-hours, or retries — those are platform concerns. Tokens come from the 6.2
vault, never the provider's own env.
"""

from __future__ import annotations

from typing import Protocol

from atlas.capabilities.notification.domain.models import Channel


class RenderedMessage(Protocol):
    title: str
    body: str
    actions: tuple[tuple[str, str], ...]   # (label, callback_url) for approvals


class NotificationProvider(Protocol):
    name: str
    supports_actions: bool     # can it render Approve/Deny buttons?

    async def initialize(self) -> None: ...
    async def health(self) -> bool: ...
    async def send(self, channel: Channel, title: str, body: str,
                   *, actions: tuple[tuple[str, str], ...] = ()) -> bool: ...
    async def shutdown(self) -> None: ...
