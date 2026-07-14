"""CalDAV calendar provider — stub adapter behind the CalendarProvider protocol.

Ships as a verified stub: implements the full protocol, raises NotImplementedError
on all methods, proving the contract is satisfiable by CalDAV without requiring
the caldav/icalendar libraries in this phase. A future implementation replaces the
bodies; the protocol surface is the invariant.
"""
from __future__ import annotations

from datetime import datetime

from atlas.capabilities.domain.calendar import (
    Availability,
    CalendarEvent,
    EventDraft,
)


class CalDAVProvider:
    """CalDAV stub — full provider protocol surface, bodies deferred.
    Replace bodies with caldav/icalendar logic when the CalDAV account is tested."""
    name = "caldav"
    requires_auth = True

    def __init__(self, server_url: str, credential_id: str) -> None:
        self._url = server_url
        self._credential_id = credential_id

    async def initialize(self) -> None:
        pass

    async def authenticate(self) -> None:
        pass  # future: fetch app password from Identity vault

    async def health(self) -> bool:
        return False  # not yet connected

    async def list_events(self, calendar_id: str, *, start: datetime,
                          end: datetime, limit: int) -> list[CalendarEvent]:
        raise NotImplementedError("CalDAV adapter not implemented")

    async def search(self, query: str, *, limit: int) -> list[CalendarEvent]:
        raise NotImplementedError("CalDAV adapter not implemented")

    async def get_event(self, calendar_id: str, event_id: str) -> CalendarEvent:
        raise NotImplementedError("CalDAV adapter not implemented")

    async def free_busy(self, calendar_id: str, *, start: datetime,
                        end: datetime) -> Availability:
        return Availability(calendar_id=calendar_id, window_start=start,
                            window_end=end, busy=())

    async def create_event(self, draft: EventDraft) -> str:
        raise NotImplementedError("CalDAV adapter not implemented")

    async def update_event(self, draft: EventDraft) -> str:
        raise NotImplementedError("CalDAV adapter not implemented")

    async def delete_event(self, calendar_id: str, event_id: str) -> None:
        raise NotImplementedError("CalDAV adapter not implemented")

    async def shutdown(self) -> None:
        pass
