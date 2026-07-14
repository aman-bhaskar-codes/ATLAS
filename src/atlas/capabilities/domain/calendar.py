"""Calendar domain models — provider-neutral.

WHY EventDraft is distinct from CalendarEvent: a Draft is what we intend to commit
(the thing the preview renders and the user approves); an Event is what already
exists on the calendar. Compose = build a Draft (Tier-0, no side effect); commit =
create/update/delete (Tier-2, has consequences and reaches other humans via invites).
WHY EventTime is its own type: an event is either timed (start/end + tz) or all-day
(date only). Conflating them causes the classic off-by-one all-day bug.
"""
from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum

from pydantic import BaseModel, EmailStr


class AttendeeResponse(StrEnum):
    NEEDS_ACTION = "needs_action"
    ACCEPTED = "accepted"
    DECLINED = "declined"
    TENTATIVE = "tentative"


class Attendee(BaseModel):
    model_config = {"frozen": True}
    email: EmailStr
    name: str | None = None
    optional: bool = False
    organizer: bool = False
    response: AttendeeResponse = AttendeeResponse.NEEDS_ACTION
    known: bool = False  # set by the platform against the known-contacts set

    def render(self) -> str:
        base = f"{self.name} <{self.email}>" if self.name else f"{self.email}"
        return base + (" (optional)" if self.optional else "")


class EventTime(BaseModel):
    """Timed OR all-day. Exactly one of (start_dt/end_dt) or (start_date/end_date)."""
    model_config = {"frozen": True}
    start_dt: datetime | None = None
    end_dt: datetime | None = None
    start_date: date | None = None      # all-day
    end_date: date | None = None        # all-day (exclusive, per iCal)
    tz: str = "UTC"

    @property
    def all_day(self) -> bool:
        return self.start_date is not None

    def render(self) -> str:
        if self.all_day:
            return f"{self.start_date} (all day)"
        if self.start_dt and self.end_dt:
            return f"{self.start_dt:%Y-%m-%d %H:%M}-{self.end_dt:%H:%M} {self.tz}"
        return "(unset)"


class CalendarEvent(BaseModel):
    model_config = {"frozen": True}
    id: str
    calendar_id: str
    title: str = ""
    description: str = ""
    location: str = ""
    when: EventTime
    attendees: tuple[Attendee, ...] = ()
    organizer: Attendee | None = None
    conferencing: str | None = None      # e.g. a Meet/Zoom link (normalized to a string URL)
    recurrence: tuple[str, ...] = ()      # raw RRULE strings, opaque but preserved
    status: str = "confirmed"


class EventDraft(BaseModel):
    """The intended write. This is what the preview renders and the user approves.
    event_id set => update; event_id set + delete flag on the call => delete."""
    model_config = {"frozen": True}
    calendar_id: str = "primary"
    title: str = ""
    description: str = ""
    location: str = ""
    when: EventTime
    attendees: tuple[Attendee, ...] = ()
    conferencing_request: bool = False    # ask the provider to attach a video link
    recurrence: tuple[str, ...] = ()
    event_id: str | None = None           # None => create; set => update
    send_invites: bool = True             # whether attendees are notified


class FreeBusySlot(BaseModel):
    model_config = {"frozen": True}
    start: datetime
    end: datetime


class Availability(BaseModel):
    """Result of a free/busy query over a window. `busy` are the taken slots;
    the caller computes gaps against its own working-hours policy."""
    model_config = {"frozen": True}
    calendar_id: str
    window_start: datetime
    window_end: datetime
    busy: tuple[FreeBusySlot, ...] = ()
