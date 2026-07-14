"""Channel Identity Resolver.

Resolves which channels should be targeted for a given notification.
"""

from __future__ import annotations

from atlas.capabilities.notification.domain.models import Notification
from atlas.capabilities.notification.registry import NotificationRegistry


class ChannelResolver:
    def __init__(self, registry: NotificationRegistry) -> None:
        self._registry = registry

    def resolve(self, n: Notification, *, multi: bool) -> tuple[str, ...]:
        """Resolve channels. If notification specifies explicitly, respect them.
        Otherwise, gather eligible channels based on priority floor."""
        
        if n.channels:
            # Validate explicit channels against priority floor? 
            # Usually explicit overrides priority floor.
            valid = [ch for ch in n.channels if self._registry.channel(ch)]
            return tuple(valid)
            
        # Fallback to all eligible channels
        eligible = []
        for ch in self._registry.all_channels():
            if n.priority >= ch.priority_floor:
                eligible.append(ch.name)
                
        return tuple(eligible)
