"""Quiet hours engine — tier-aware, timezone-aware, deterministic.

WHY Tier-2/3 interruption is hard-coded (not config): a safety confirmation must
never be silenceable by a schedule. Tier-0 always digests; Tier-1 digests unless
urgent. Windows are evaluated against the injected Clock in the user's tz so tests
are deterministic and DST is handled by zoneinfo.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import time
from zoneinfo import ZoneInfo

from atlas.capabilities.notification.domain.models import Notification, NotificationPriority
from atlas.infra.clock import Clock


@dataclass(frozen=True)
class QuietWindow:
    start: time
    end: time
    tz: str


class QuietHoursEngine:
    def __init__(self, windows: list[QuietWindow], clock: Clock) -> None:
        self._windows = windows
        self._clock = clock

    def in_quiet_hours(self) -> bool:
        for w in self._windows:
            now = self._clock.now().astimezone(ZoneInfo(w.tz)).time()
            inside = (w.start <= now < w.end) if w.start <= w.end \
                else (now >= w.start or now < w.end)   # window crosses midnight
            if inside:
                return True
        return False

    def should_interrupt(self, n: Notification) -> bool:
        """True = deliver now; False = batch into digest."""
        if n.priority >= NotificationPriority.HIGH:      # Tier 2/3: ALWAYS interrupt
            return True
        if not self.in_quiet_hours():
            return True
        if n.priority == NotificationPriority.NORMAL and n.urgent:  # Tier 1 urgent escapes
            return True
        return False                                     # Tier 0, or non-urgent Tier 1 -> digest
