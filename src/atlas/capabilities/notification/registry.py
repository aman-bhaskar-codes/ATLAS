"""Notification Provider Registry.

Registers available providers and channels, and ranks channels by preference.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from atlas.capabilities.notification.domain.models import Channel

if TYPE_CHECKING:
    from atlas.capabilities.notification.providers.base import NotificationProvider


class NotificationRegistry:
    def __init__(self) -> None:
        self._providers: dict[str, NotificationProvider] = {}
        self._channels: dict[str, Channel] = {}
        self._preferences: dict[str, int] = {}  # provider_name -> rank (higher is better)

    def register_provider(self, provider: NotificationProvider, rank: int = 10) -> None:
        self._providers[provider.name] = provider
        self._preferences[provider.name] = rank

    def register_channel(self, channel: Channel) -> None:
        self._channels[channel.name] = channel

    def provider_for(self, channel_name: str) -> NotificationProvider | None:
        channel = self._channels.get(channel_name)
        if not channel:
            return None
        return self._providers.get(channel.provider)

    def channel(self, name: str) -> Channel | None:
        return self._channels.get(name)

    def all_channels(self) -> list[Channel]:
        return list(self._channels.values())

    def rank_channels(self, names: tuple[str, ...]) -> tuple[str, ...]:
        """Rank requested channels by provider preference."""
        valid = [n for n in names if n in self._channels]
        # Sort by preference descending
        valid.sort(key=lambda n: self._preferences.get(self._channels[n].provider, 0), reverse=True)
        return tuple(valid)
