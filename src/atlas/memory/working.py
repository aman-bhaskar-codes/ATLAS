"""Working memory — ephemeral per-task scratchpad.

WHY in-process + bounded: this is the 'RAM' of a single task. It's cleared when
the task ends and capped (ring buffer) so a runaway loop can't exhaust memory.
Nothing here is persisted; durable history lives in episodic.
"""

from __future__ import annotations

from collections import deque

from atlas.memory.types import Episode


class WorkingMemory:
    def __init__(self, max_items: int = 100) -> None:
        self._items: deque[Episode] = deque(maxlen=max_items)

    def add(self, episode: Episode) -> None:
        self._items.append(episode)

    def recent(self, n: int = 20) -> tuple[Episode, ...]:
        return tuple(list(self._items)[-n:])

    def clear(self) -> None:
        self._items.clear()
