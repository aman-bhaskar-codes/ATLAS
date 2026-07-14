"""Time Intelligence — the ONE place time logic lives.

WHY centralized: DST, tz conversion, business hours, weekends, and (future)
holidays must never be reimplemented ad-hoc. Everything scheduling-related asks
this service. Uses zoneinfo (stdlib) for correct DST; the injected Clock for 'now'.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from atlas.infra.clock import Clock


@dataclass(frozen=True)
class WorkingHours:
    start: time                 # e.g. 09:00
    end: time                   # e.g. 18:00
    workdays: frozenset[int]    # 0=Mon .. 6=Sun
    tz: str


class TimeIntelligence:
    def __init__(self, clock: Clock, working_hours: WorkingHours,
                 holidays: frozenset[str] = frozenset()) -> None:
        self._clock = clock
        self._wh = working_hours
        self._holidays = holidays        # ISO dates 'YYYY-MM-DD' (future: holiday calendars)

    def now(self, tz: str | None = None) -> datetime:
        n = self._clock.now()
        return n.astimezone(ZoneInfo(tz)) if tz else n

    def to_tz(self, dt: datetime, tz: str) -> datetime:
        return dt.astimezone(ZoneInfo(tz))

    def is_working_time(self, dt: datetime) -> bool:
        local = dt.astimezone(ZoneInfo(self._wh.tz))
        if local.date().isoformat() in self._holidays:
            return False
        if local.weekday() not in self._wh.workdays:
            return False
        return self._wh.start <= local.time() < self._wh.end

    def next_working_slot(self, after: datetime, duration: timedelta) -> datetime:
        """First working-hours start at/after `after` that fits `duration`."""
        cur = after.astimezone(ZoneInfo(self._wh.tz))
        for _ in range(14 * 24):                     # bounded search, 2 weeks of hours
            if (self.is_working_time(cur)
                    and self.is_working_time(cur + duration - timedelta(minutes=1))):
                return cur
            cur += timedelta(minutes=30)
        return after                                 # fallback: caller handles
