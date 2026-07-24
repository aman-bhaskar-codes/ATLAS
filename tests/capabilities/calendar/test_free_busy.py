"""Tests for CalendarPlatform.find_free_slots — provider-agnostic arithmetic."""
from __future__ import annotations

from datetime import UTC, datetime

import pytest

from atlas.capabilities.domain.calendar import CalendarEvent, EventTime, FreeBusySlot
from atlas.capabilities.domain.contacts import KnownContacts
from atlas.capabilities.platforms.calendar_platform import CalendarPlatform

_TZ = UTC


def _dt(h: int, m: int = 0) -> datetime:
    return datetime(2026, 7, 15, h, m, tzinfo=_TZ)


def _event(start_h: int, end_h: int, eid: str = "x") -> CalendarEvent:
    return CalendarEvent(
        id=eid, calendar_id="primary", title="busy",
        when=EventTime(start_dt=_dt(start_h), end_dt=_dt(end_h)))


class FakeProvider:
    name = "fake"
    requires_auth = False
    async def initialize(self) -> None: ...
    async def authenticate(self) -> None: ...
    async def health(self) -> bool: return True
    async def list_events(self, cal_id: str, *, start: datetime,
                          end: datetime, limit: int) -> list[CalendarEvent]: return []
    async def search(self, query: str, *, limit: int) -> list[CalendarEvent]: return []
    async def get_event(self, calendar_id: str, event_id: str) -> CalendarEvent:
        raise NotImplementedError
    async def free_busy(self, cal_id: str, *, start: datetime, end: datetime):  # type: ignore
        from atlas.capabilities.domain.calendar import Availability
        return Availability(calendar_id=cal_id, window_start=start, window_end=end)
    async def create_event(self, draft: object) -> str: return "x"
    async def update_event(self, draft: object) -> str: return "x"
    async def delete_event(self, calendar_id: str, event_id: str) -> None: ...
    async def shutdown(self) -> None: ...


class DenyNotify:
    async def request_approval(self, req: object, channels: tuple[str, ...]) -> object:
        from datetime import datetime

        from atlas.capabilities.notification.domain.models import ApprovalDecision, ApprovalRequest
        assert isinstance(req, ApprovalRequest)
        return ApprovalDecision(
            request_id=req.id, approved=False,
            decided_ts=datetime.now(UTC))


class FakeIds:
    def execution_id(self) -> str: return "e1"
    def correlation_id(self) -> object: return object()
    def task_id(self) -> str: return "t1"


def _platform() -> CalendarPlatform:
    return CalendarPlatform(
        provider=FakeProvider(), notifications=DenyNotify(),  # type: ignore[arg-type]
        ids=FakeIds(), known=KnownContacts(set()),  # type: ignore[arg-type]
        approval_channels=())


@pytest.mark.asyncio
async def test_find_free_slots_returns_gaps() -> None:
    """Two busy blocks from 10-11 and 14-15 -> gaps before/between/after."""
    platform = _platform()
    # Inject busy blocks by monkeypatching free_busy
    from atlas.capabilities.domain.calendar import Availability
    async def _fake_free_busy(cal_id: str, *, start: datetime, end: datetime) -> Availability:
        return Availability(
            calendar_id=cal_id, window_start=start, window_end=end,
            busy=(FreeBusySlot(start=_dt(10), end=_dt(11)),
                  FreeBusySlot(start=_dt(14), end=_dt(15))))
    platform._provider.free_busy = _fake_free_busy  # type: ignore

    slots = await platform.find_free_slots(start=_dt(9), end=_dt(17), min_minutes=30)
    # Expect gaps: 09-10, 11-14, 15-17
    assert len(slots) == 3
    assert slots[0].start == _dt(9)
    assert slots[0].end == _dt(10)
    assert slots[1].start == _dt(11)
    assert slots[1].end == _dt(14)
    assert slots[2].start == _dt(15)
    assert slots[2].end == _dt(17)


@pytest.mark.asyncio
async def test_back_to_back_busy_yields_no_slot() -> None:
    """Continuous busy from 9-17 -> no free slots >= 30 min."""
    platform = _platform()
    from atlas.capabilities.domain.calendar import Availability
    async def _full(cal_id: str, *, start: datetime, end: datetime) -> Availability:
        return Availability(
            calendar_id=cal_id, window_start=start, window_end=end,
            busy=(FreeBusySlot(start=_dt(9), end=_dt(17)),))
    platform._provider.free_busy = _full  # type: ignore

    slots = await platform.find_free_slots(start=_dt(9), end=_dt(17), min_minutes=30)
    assert slots == []


@pytest.mark.asyncio
async def test_no_busy_blocks_full_window_free() -> None:
    """No busy blocks -> entire window is one free slot."""
    platform = _platform()
    slots = await platform.find_free_slots(start=_dt(9), end=_dt(17), min_minutes=30)
    assert len(slots) == 1
    assert slots[0].start == _dt(9)
    assert slots[0].end == _dt(17)
