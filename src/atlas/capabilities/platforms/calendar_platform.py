"""Calendar Platform — read/search/free-busy are Tier-1; COMMIT is the guarded path.

COMMIT PIPELINE (mirrors 6.5 email send):
  1. classify attendees: any NOT in known contacts? -> escalate the preview
  2. detect conflicts against existing events in the window -> surface in preview
  3. build a REAL preview (actual title/time/attendees/location/conferencing)
  4. request approval via the 6.4 platform (deny-on-timeout; new attendees => stronger warning)
  5. only on approval does the provider create/update/delete
Compose is Tier-0 (build an EventDraft, no side effect).
"""
from __future__ import annotations

from datetime import datetime

from atlas.capabilities.domain.calendar import (
    Availability,
    CalendarEvent,
    EventDraft,
    FreeBusySlot,
)
from atlas.capabilities.domain.contacts import KnownContacts
from atlas.capabilities.errors import CapabilityDenied
from atlas.capabilities.notification.domain.models import ApprovalRequest
from atlas.capabilities.notification.platform import NotificationPlatform
from atlas.capabilities.providers.calendar.base import CalendarProvider
from atlas.infra.ids import CorrelationId, IdGenerator
from atlas.infra.logging import get_logger

_log = get_logger("atlas.calendar")


class CalendarPlatform:
    def __init__(
        self, *, provider: CalendarProvider, notifications: NotificationPlatform,
        ids: IdGenerator, known: KnownContacts, approval_channels: tuple[str, ...],
        default_calendar: str = "primary",
    ) -> None:
        self._provider = provider
        self._notify = notifications
        self._ids = ids
        self._known = known
        self._approval_channels = approval_channels
        self._default = default_calendar

    # ---- reads (Tier-1) ----------------------------------------------------
    async def list_events(self, *, start: datetime, end: datetime,
                          calendar_id: str | None = None, limit: int = 50) -> list[CalendarEvent]:
        return await self._provider.list_events(
            calendar_id or self._default, start=start, end=end, limit=limit)

    async def search(self, query: str, *, limit: int = 20) -> list[CalendarEvent]:
        return await self._provider.search(query, limit=limit)

    async def free_busy(self, *, start: datetime, end: datetime,
                        calendar_id: str | None = None) -> Availability:
        return await self._provider.free_busy(
            calendar_id or self._default, start=start, end=end)

    async def find_free_slots(self, *, start: datetime, end: datetime,
                              min_minutes: int) -> list[FreeBusySlot]:
        """Convenience: gaps >= min_minutes in the window, computed from busy blocks.
        WHY here (not in the adapter): it's provider-agnostic arithmetic over Availability."""
        avail = await self.free_busy(start=start, end=end)
        free: list[FreeBusySlot] = []
        cursor = start
        for b in sorted(avail.busy, key=lambda s: s.start):
            if (b.start - cursor).total_seconds() >= min_minutes * 60:
                free.append(FreeBusySlot(start=cursor, end=b.start))
            cursor = max(cursor, b.end)
        if (end - cursor).total_seconds() >= min_minutes * 60:
            free.append(FreeBusySlot(start=cursor, end=end))
        return free

    # ---- COMMIT (Tier-2, previewed, human-approved) ------------------------
    async def commit(self, draft: EventDraft, correlation_id: CorrelationId,
                     *, delete: bool = False) -> str | None:
        action = "delete" if delete else ("update" if draft.event_id else "create")
        unknown = [a for a in draft.attendees if not self._known.is_known(str(a.email))]
        conflicts = await self._conflicts(draft) if not delete else []
        preview = self._render_preview(draft, action, unknown, conflicts)

        req = ApprovalRequest(
            id=self._ids.execution_id(), correlation_id=correlation_id,
            prompt=self._prompt(action, unknown),
            detail=preview, timeout_s=600.0, default_on_timeout=False)
        decision = await self._notify.request_approval(req, self._approval_channels)
        if not decision.approved:
            _log.info(
                "calendar.commit_denied", event_type="calendar",
                correlation_id=str(correlation_id), action=action,
                timed_out=decision.timed_out, unknown_attendees=len(unknown))
            raise CapabilityDenied(
                f"{action} not approved" + (" (timed out)" if decision.timed_out else ""))

        if delete:
            await self._provider.delete_event(draft.calendar_id, draft.event_id or "")
            _log.info("calendar.deleted",
                      correlation_id=str(correlation_id), event_id=draft.event_id)
            return None

        event_id = (await self._provider.update_event(draft) if draft.event_id
                    else await self._provider.create_event(draft))
        _log.info("calendar.committed", event_type="calendar",
                  correlation_id=str(correlation_id), action=action,
                  event_id=event_id, attendees=len(draft.attendees))
        return event_id

    async def _conflicts(self, draft: EventDraft) -> list[CalendarEvent]:
        if draft.when.all_day or draft.when.start_dt is None or draft.when.end_dt is None:
            return []
        existing = await self._provider.list_events(
            draft.calendar_id, start=draft.when.start_dt, end=draft.when.end_dt, limit=10)
        return [e for e in existing if e.id != draft.event_id]

    def _prompt(self, action: str, unknown: list[object]) -> str:
        if unknown:
            return f"⚠️ {action.title()} event inviting {len(unknown)} NEW contact(s)?"
        return f"{action.title()} this event?"

    def _render_preview(self, draft: EventDraft, action: str,
                        unknown: list[object], conflicts: list[CalendarEvent]) -> str:
        lines = [
            f"─── CALENDAR {action.upper()} PREVIEW ───",
            f"Title:    {draft.title}",
            f"When:     {draft.when.render()}",
        ]
        if draft.location:
            lines.append(f"Location: {draft.location}")
        if draft.conferencing_request:
            lines.append("Video:    (a meeting link will be attached)")
        if draft.attendees:
            lines.append("Attendees: " + ", ".join(a.render() for a in draft.attendees))
        if unknown:
            lines.append("")
            lines.append("⚠️ NEW CONTACTS (not in your known list): "
                         + ", ".join(str(getattr(a, 'email', a)) for a in unknown))
        if conflicts:
            lines.append("")
            lines.append("⚠️ CONFLICTS with: "
                         + "; ".join(f"{c.title} ({c.when.render()})" for c in conflicts))
        if draft.description:
            lines += ["", draft.description]
        return "\n".join(lines)
