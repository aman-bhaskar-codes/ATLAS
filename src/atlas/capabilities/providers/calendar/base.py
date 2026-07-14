"""CalendarProvider protocol — one contract for Google Calendar, CalDAV, Outlook (later).

WHY the same 7-method shape as the 6.1 Provider plus typed read/write methods:
consistency with the dispatcher/health/registry, and a typed surface the platform
calls directly. Providers hold only a credential id (6.2) — never a secret.
"""
from __future__ import annotations

from datetime import datetime
from typing import Protocol

from atlas.capabilities.domain.calendar import (
    Availability,
    CalendarEvent,
    EventDraft,
)


class CalendarProvider(Protocol):
    name: str
    requires_auth: bool

    async def initialize(self) -> None: ...
    async def authenticate(self) -> None: ...
    async def health(self) -> bool: ...

    # reads (Tier-1)
    async def list_events(self, calendar_id: str, *, start: datetime,
                          end: datetime, limit: int) -> list[CalendarEvent]: ...
    async def search(self, query: str, *, limit: int) -> list[CalendarEvent]: ...
    async def get_event(self, calendar_id: str, event_id: str) -> CalendarEvent: ...
    async def free_busy(self, calendar_id: str, *, start: datetime,
                        end: datetime) -> Availability: ...

    # writes (Tier-2, only reached after platform preview+approval)
    async def create_event(self, draft: EventDraft) -> str: ...   # returns event id
    async def update_event(self, draft: EventDraft) -> str: ...
    async def delete_event(self, calendar_id: str, event_id: str) -> None: ...

    async def shutdown(self) -> None: ...
