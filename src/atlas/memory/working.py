"""Working memory — ephemeral per-task scratchpad.

WHY in-process + bounded: this is the 'RAM' of a single task. It's cleared when
the task ends and capped (ring buffer) so a runaway loop can't exhaust memory.
Nothing here is persisted; durable history lives in episodic.
"""

from __future__ import annotations

from collections import deque

from atlas.infra.bus import MessageBus
from atlas.memory.types import Episode


class WorkingMemory:
    def __init__(self, max_items: int = 100) -> None:
        self._items: deque[Episode] = deque(maxlen=max_items)
        self._bus: MessageBus | None = None

    def set_bus(self, bus: MessageBus) -> None:
        """Connect to the MessageBus. WorkingMemory is per-task and ephemeral,
        so this stores the reference for potential future event publishing."""
        self._bus = bus

    def add(self, episode: Episode) -> None:
        self._items.append(episode)

    def recent(self, n: int = 20) -> tuple[Episode, ...]:
        return tuple(list(self._items)[-n:])

    def clear(self) -> None:
        self._items.clear()
