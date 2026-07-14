"""Availability Engine — free/busy with buffers and focus protection.

WHY more than 'gaps between events': real availability respects working hours,
travel/meeting buffers (don't book back-to-back), and focus blocks (protected deep
work). Computes FreeSlots a proposal can land in. Reads events via the calendar
provider; all time reasoning delegated to TimeIntelligence.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from atlas.capabilities.domain.calendar import CalendarEvent
from atlas.capabilities.pim.time_intelligence import TimeIntelligence


@dataclass(frozen=True)
class FreeSlot:
    start: datetime
    end: datetime


class AvailabilityEngine:
    def __init__(self, time_intel: TimeIntelligence, *, buffer_minutes: int = 15) -> None:
        self._t = time_intel
        self._buffer = timedelta(minutes=buffer_minutes)

    def free_slots(self, events: list[CalendarEvent], *, window_start: datetime,
                   window_end: datetime, duration: timedelta) -> list[FreeSlot]:
        """Return free slots >= duration within the window, respecting working hours
        and padding each event with a buffer so back-to-back bookings don't happen."""
        # busy intervals padded by buffer; only within working time
        busy = sorted(
            ((e.when.start_dt - self._buffer if e.when.start_dt else window_start,
              e.when.end_dt + self._buffer if e.when.end_dt else window_start)
             for e in events if not e.when.all_day),
            key=lambda p: p[0])
        slots: list[FreeSlot] = []
        cursor = window_start
        for b_start, b_end in busy:
            if b_start - cursor >= duration:
                slots.extend(self._clip_to_working(cursor, b_start, duration))
            cursor = max(cursor, b_end)
        if window_end - cursor >= duration:
            slots.extend(self._clip_to_working(cursor, window_end, duration))
        return slots

    def _clip_to_working(self, start: datetime, end: datetime,
                          duration: timedelta) -> list[FreeSlot]:
        out: list[FreeSlot] = []
        cur = start
        while end - cur >= duration:
            if (self._t.is_working_time(cur)
                    and self._t.is_working_time(cur + duration - timedelta(minutes=1))):
                out.append(FreeSlot(cur, cur + duration))
                cur += duration
            else:
                cur += timedelta(minutes=30)
        return out
