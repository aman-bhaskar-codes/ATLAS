"""CronScheduler.tick() must fire each due schedule at most once per cron-minute.

cron_matches is minute-granular, so every tick() landing in the same minute
matches the same schedules. Without a per-minute guard a sub-minute tick
interval (or an extra manual tick) would double-dispatch. These tests pin the
one-fire-per-minute behaviour for both DB schedules and in-process jobs.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from atlas.infra.db import Database
from atlas.infra.ids import UuidGenerator
from atlas.infra.scheduler import CronScheduler


class _FixedClock:
    """Clock whose `now()` is set explicitly, so a test can hold or advance time."""

    def __init__(self, at: datetime) -> None:
        self._at = at

    def now(self) -> datetime:
        return self._at

    def set(self, at: datetime) -> None:
        self._at = at


@pytest.mark.asyncio
async def test_db_schedule_fires_once_per_minute(memory_db: Database) -> None:
    clock = _FixedClock(datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC))
    cron = CronScheduler(db=memory_db, ids=UuidGenerator(), clock=clock)  # type: ignore[arg-type]
    await cron.add_schedule(
        description="every minute", cron_expression="* * * * *", task_template={"request": "go"}
    )

    first = await cron.tick()
    assert len(first) == 1  # due this minute

    # Second tick in the SAME minute must not re-fire.
    second = await cron.tick()
    assert second == []

    # Advancing to the next minute fires again.
    clock.set(clock.now() + timedelta(minutes=1))
    third = await cron.tick()
    assert len(third) == 1


@pytest.mark.asyncio
async def test_in_process_job_fires_once_per_minute(memory_db: Database) -> None:
    clock = _FixedClock(datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC))
    cron = CronScheduler(db=memory_db, ids=UuidGenerator(), clock=clock)  # type: ignore[arg-type]

    calls = 0

    async def _job() -> None:
        nonlocal calls
        calls += 1

    cron.register_job("maint", "* * * * *", _job)

    await cron.tick()
    await cron.tick()  # same minute
    assert calls == 1

    clock.set(clock.now() + timedelta(minutes=1))
    await cron.tick()
    assert calls == 2
