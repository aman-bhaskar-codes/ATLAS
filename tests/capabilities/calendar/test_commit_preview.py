"""Tests for CalendarPlatform Tier-2 commit gate — mirrors test_send_preview.py from 6.5."""
from __future__ import annotations

from datetime import UTC, datetime

import pytest

from atlas.capabilities.domain.calendar import Attendee, CalendarEvent, EventDraft, EventTime
from atlas.capabilities.domain.contacts import KnownContacts
from atlas.capabilities.errors import CapabilityDenied
from atlas.capabilities.notification.domain.models import ApprovalDecision, ApprovalRequest
from atlas.capabilities.platforms.calendar_platform import CalendarPlatform
from atlas.infra.ids import CorrelationId

_NOW = datetime(2026, 7, 15, 9, 0, tzinfo=UTC)
_END = datetime(2026, 7, 15, 10, 0, tzinfo=UTC)


class FakeCalProvider:
    name = "fake_cal"
    requires_auth = False

    def __init__(self) -> None:
        self.created: EventDraft | None = None
        self.deleted: tuple[str, str] | None = None

    async def initialize(self) -> None: ...
    async def authenticate(self) -> None: ...
    async def health(self) -> bool: return True
    async def list_events(self, cal_id: str, *, start: datetime,
                          end: datetime, limit: int) -> list[CalendarEvent]:
        return []
    async def search(self, query: str, *, limit: int) -> list[CalendarEvent]: return []
    async def get_event(self, calendar_id: str, event_id: str) -> CalendarEvent:
        raise NotImplementedError
    async def free_busy(self, cal_id: str, *, start: datetime, end: datetime):  # type: ignore
        from atlas.capabilities.domain.calendar import Availability
        return Availability(calendar_id=cal_id, window_start=start, window_end=end)
    async def create_event(self, draft: EventDraft) -> str:
        self.created = draft
        return "evt-001"
    async def update_event(self, draft: EventDraft) -> str:
        self.created = draft
        return draft.event_id or "evt-001"
    async def delete_event(self, calendar_id: str, event_id: str) -> None:
        self.deleted = (calendar_id, event_id)
    async def shutdown(self) -> None: ...


class FakeNotify:
    def __init__(self, approved: bool) -> None:
        self._a = approved
        self.previewed: str | None = None
        self.prompt: str | None = None

    async def request_approval(self, req: ApprovalRequest,
                               channels: tuple[str, ...]) -> ApprovalDecision:
        self.previewed = req.detail
        self.prompt = req.prompt
        return ApprovalDecision(
            request_id=req.id, approved=self._a,
            decided_ts=datetime.now(UTC))


class FakeIds:
    def execution_id(self) -> str: return "e1"
    def correlation_id(self) -> CorrelationId: return CorrelationId("c")
    def task_id(self) -> str: return "t1"


def _platform(approved: bool,
              known: tuple[str, ...] = ()) -> tuple[CalendarPlatform, FakeCalProvider, FakeNotify]:
    prov = FakeCalProvider()
    notify = FakeNotify(approved)
    platform = CalendarPlatform(
        provider=prov, notifications=notify, ids=FakeIds(),  # type: ignore[arg-type]
        known=KnownContacts(set(known)), approval_channels=("ntfy:atlas",))
    return platform, prov, notify


_DRAFT = EventDraft(
    title="Team Standup",
    when=EventTime(start_dt=_NOW, end_dt=_END),
    attendees=(Attendee(email="boss@corp.com"),),
)


@pytest.mark.asyncio
async def test_create_requires_approval() -> None:
    """Denied -> CapabilityDenied; provider.create_event is NEVER called."""
    platform, prov, _ = _platform(approved=False)
    with pytest.raises(CapabilityDenied):
        await platform.commit(_DRAFT, CorrelationId("c"))
    assert prov.created is None


@pytest.mark.asyncio
async def test_approved_create_calls_provider() -> None:
    """Approved -> provider.create_event called exactly once; returns event id."""
    platform, prov, _ = _platform(approved=True)
    eid = await platform.commit(_DRAFT, CorrelationId("c"))
    assert eid == "evt-001"
    assert prov.created is _DRAFT


@pytest.mark.asyncio
async def test_new_attendee_escalates_in_preview() -> None:
    """Unknown attendee -> preview contains NEW CONTACTS warning + ⚠️ prompt."""
    platform, _, notify = _platform(approved=True, known=())
    await platform.commit(_DRAFT, CorrelationId("c"))
    assert notify.previewed is not None
    assert "NEW CONTACTS" in notify.previewed
    assert notify.prompt is not None
    assert "⚠️" in notify.prompt


@pytest.mark.asyncio
async def test_known_attendee_no_new_warning() -> None:
    """Known attendee -> preview does NOT contain NEW CONTACTS warning."""
    platform, _, notify = _platform(approved=True, known=("boss@corp.com",))
    await platform.commit(_DRAFT, CorrelationId("c"))
    assert notify.previewed is not None
    assert "NEW CONTACTS" not in notify.previewed


@pytest.mark.asyncio
async def test_preview_contains_real_title_and_time() -> None:
    """Preview always shows the exact title and time — not a paraphrase."""
    platform, _, notify = _platform(approved=True)
    await platform.commit(_DRAFT, CorrelationId("c"))
    assert notify.previewed is not None
    assert "Team Standup" in notify.previewed
    assert "2026-07-15" in notify.previewed


@pytest.mark.asyncio
async def test_delete_calls_provider_after_approval() -> None:
    """Approved delete -> provider.delete_event called; no conflict check."""
    draft = EventDraft(
        title="Old Meeting", event_id="evt-old",
        when=EventTime(start_dt=_NOW, end_dt=_END))
    platform, prov, _ = _platform(approved=True)
    result = await platform.commit(draft, CorrelationId("c"), delete=True)
    assert result is None
    assert prov.deleted == ("primary", "evt-old")
