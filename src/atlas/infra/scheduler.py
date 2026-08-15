"""Cron-based task scheduler.

WHY: Vamos specifies a schedules table for recurring tasks (e.g. "weekly email
digest"). This scheduler checks which schedules are due and creates tasks from
their templates. It is designed to be called periodically (e.g. every 60s from
the main event loop or a background timer).
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from datetime import datetime

from atlas.infra.clock import Clock
from atlas.infra.db import Database
from atlas.infra.ids import IdGenerator
from atlas.infra.logging import get_logger

_log = get_logger("atlas.scheduler")

_InProcessJob = Callable[[], Awaitable[None]]


def _parse_cron_field(field: str, min_val: int, max_val: int) -> set[int]:
    """Parse a single cron field into a set of matching values."""
    if field == "*":
        return set(range(min_val, max_val + 1))
    values: set[int] = set()
    for part in field.split(","):
        if "/" in part:
            base, step_str = part.split("/", 1)
            step = int(step_str)
            start = min_val if base == "*" else int(base)
            values.update(range(start, max_val + 1, step))
        elif "-" in part:
            lo, hi = part.split("-", 1)
            values.update(range(int(lo), int(hi) + 1))
        else:
            values.add(int(part))
    return values


def cron_matches(expression: str, dt: datetime) -> bool:
    """Check if a datetime matches a 5-field cron expression (min hour dom month dow)."""
    fields = expression.strip().split()
    if len(fields) != 5:
        return False
    minute, hour, dom, month, dow = fields
    return (
        dt.minute in _parse_cron_field(minute, 0, 59)
        and dt.hour in _parse_cron_field(hour, 0, 23)
        and dt.day in _parse_cron_field(dom, 1, 31)
        and dt.month in _parse_cron_field(month, 1, 12)
        and dt.weekday() in _parse_cron_field(dow, 0, 6)
    )


class CronScheduler:
    def __init__(self, db: Database, ids: IdGenerator, clock: Clock) -> None:
        self._db = db
        self._ids = ids
        self._clock = clock
        # In-process jobs: name -> (cron_expression, callable)
        self._jobs: dict[str, tuple[str, _InProcessJob]] = {}

    def register_job(
        self, name: str, cron: str, fn: _InProcessJob
    ) -> None:
        """Register an in-process recurring job (not persisted to DB).

        The job function is called by tick() when the cron expression matches.
        This is for internal system maintenance jobs (consolidation, pruning, etc.)
        that shouldn't appear as user-visible schedules.
        """
        self._jobs[name] = (cron, fn)
        _log.info("scheduler.job_registered", event_type="scheduler",
                  name=name, cron=cron)

    async def add_schedule(
        self, *, description: str, cron_expression: str,
        task_template: dict[str, object],
    ) -> str:
        """Register a new recurring schedule."""
        sid = self._ids.execution_id()
        now = self._clock.now()
        await self._db.conn.execute(
            "INSERT INTO schedules(id, description, cron_expression, task_template, "
            "enabled, next_run_ts, created_ts) VALUES (?,?,?,?,?,?,?)",
            (sid, description, cron_expression, json.dumps(task_template),
             1, now.isoformat(), now.isoformat()),
        )
        await self._db.conn.commit()
        _log.info("scheduler.added", event_type="scheduler", schedule_id=sid,
                   description=description, cron=cron_expression)
        return sid

    async def list_schedules(self) -> list[dict[str, object]]:
        cur = await self._db.conn.execute(
            "SELECT * FROM schedules ORDER BY created_ts DESC"
        )
        return [dict(r) for r in await cur.fetchall()]

    async def toggle(self, schedule_id: str, enabled: bool) -> None:
        await self._db.conn.execute(
            "UPDATE schedules SET enabled=? WHERE id=?", (int(enabled), schedule_id)
        )
        await self._db.conn.commit()

    async def tick(self) -> list[dict[str, object]]:
        """Check all enabled schedules and return task templates for those that are due.

        Call this periodically (e.g. every 60 seconds). Returns a list of
        task_template dicts that should be dispatched. Also invokes any
        registered in-process jobs whose cron expression matches.
        """
        now = self._clock.now()
        cur = await self._db.conn.execute(
            "SELECT * FROM schedules WHERE enabled=1"
        )
        schedules = [dict(r) for r in await cur.fetchall()]
        due: list[dict[str, object]] = []

        for sched in schedules:
            if cron_matches(str(sched["cron_expression"]), now):
                template = json.loads(str(sched["task_template"]))
                due.append(template)
                await self._db.conn.execute(
                    "UPDATE schedules SET last_run_ts=?, next_run_ts=? WHERE id=?",
                    (now.isoformat(), now.isoformat(), sched["id"]),
                )
                _log.info("scheduler.triggered", event_type="scheduler",
                           schedule_id=sched["id"], description=sched["description"])

        if due:
            await self._db.conn.commit()

        # Run in-process jobs
        for name, (cron_expr, fn) in self._jobs.items():
            if cron_matches(cron_expr, now):
                _log.info("scheduler.job_triggered", event_type="scheduler", name=name)
                try:
                    await fn()
                except Exception as exc:
                    _log.error("scheduler.job_error", event_type="scheduler",
                               name=name, error=str(exc))

        return due
