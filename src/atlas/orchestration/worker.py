"""Worker process — consumes the durable task queue.

Runs the SAME Orchestrator pipeline as the in-process path; the only
difference is where the InboundEvent comes from (queue row instead of an
HTTP handler). Horizontal scale = start N workers against the same database;
claim atomicity prevents double execution. Failures retry up to the job's
max_attempts, then park in 'dead' for inspection — never silently dropped.
"""

from __future__ import annotations

import asyncio
import socket
from typing import Any

from atlas.infra.backends import SQLiteConnection
from atlas.infra.logging import get_logger
from atlas.infra.queue import DurableTaskQueue
from atlas.infra.types import InboundEvent
from atlas.orchestration.types import TaskResult

_log = get_logger("atlas.worker")

_POLL_INTERVAL_S = 0.5


class TaskWorker:
    def __init__(
        self,
        *,
        orchestrator: Any,
        conn: SQLiteConnection,
        worker_id: str | None = None,
        poll_interval_s: float = _POLL_INTERVAL_S,
    ) -> None:
        self._orchestrator = orchestrator
        self._queue = DurableTaskQueue(conn, worker_id or f"{socket.gethostname()}:{id(self):x}")
        self._interval = poll_interval_s
        self._stop = asyncio.Event()

    async def stop(self) -> None:
        self._stop.set()

    async def run_forever(self) -> None:
        _log.info("worker.started", event_type="worker", worker=self._queue._worker)
        while not self._stop.is_set():
            job = await self._queue.claim()
            if job is None:
                try:
                    await asyncio.wait_for(self._stop.wait(), timeout=self._interval)
                except TimeoutError:
                    pass
                continue
            await self._execute(job)

    async def _execute(self, job: Any) -> TaskResult | None:
        _log.info("worker.job_started", event_type="worker", job_id=job.id, attempts=job.attempts)
        try:
            event = InboundEvent(
                correlation_id=job.payload["correlation_id"],
                source=job.payload["source"],
                content=job.payload["content"],
            )
            result: TaskResult = await self._orchestrator.run(event)
            await self._queue.complete(job.id)
            _log.info("worker.job_completed", event_type="worker", job_id=job.id, ok=result.ok)
            return result
        except Exception as exc:
            state = await self._queue.fail(
                job.id,
                attempts=job.attempts,
                max_attempts=job.max_attempts,
            )
            _log.error(
                "worker.job_failed",
                event_type="worker",
                job_id=job.id,
                attempts=job.attempts,
                next_state=state,
                error=repr(exc),
            )
            return None
