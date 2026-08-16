"""Phase 9 tests — durable queue, worker, placeholder translation, resume gating."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from atlas.infra.backends import SQLiteConnection, translate_placeholders
from atlas.infra.db import Database
from atlas.infra.queue import DurableTaskQueue
from atlas.orchestration.registry import ToolMetadata, ToolRegistry
from atlas.orchestration.resume import assess_resume_safety, plan_from_checkpoint
from atlas.orchestration.types import Plan, PlanStep
from atlas.orchestration.worker import TaskWorker


@pytest.fixture
async def db(tmp_path: Path):
    d = Database(tmp_path / "queue.db")
    await d.start()
    yield d
    await d.stop()


def _payload(n: str = "1") -> dict[str, Any]:
    return {"correlation_id": f"corr-{n}", "source": "api", "content": f"do {n}"}


class TestDurableTaskQueue:
    async def test_enqueue_claim_complete(self, db: Database) -> None:
        q = DurableTaskQueue(SQLiteConnection(db.conn), "w1")
        job_id = await q.enqueue(_payload())
        job = await q.claim()
        assert job is not None and job.id == job_id
        assert job.payload["content"] == "do 1"
        assert job.attempts == 1
        await q.complete(job.id)
        assert (await q.claim()) is None  # nothing pending
        assert (await q.stats())["done"] == 1

    async def test_fifo_order(self, db: Database) -> None:
        q = DurableTaskQueue(SQLiteConnection(db.conn), "w1")
        await q.enqueue(_payload("1"))
        await q.enqueue(_payload("2"))
        first = await q.claim()
        second = await q.claim()
        assert first is not None and second is not None
        assert first.payload["content"] == "do 1"
        assert second.payload["content"] == "do 2"

    async def test_retry_then_dead(self, db: Database) -> None:
        q = DurableTaskQueue(SQLiteConnection(db.conn), "w1")
        await q.enqueue(_payload(), max_attempts=2)
        job = await q.claim()
        assert job is not None
        state = await q.fail(job.id, attempts=job.attempts, max_attempts=2)
        assert state == "pending"  # attempt 1 of 2 -> retry
        job2 = await q.claim()
        assert job2 is not None and job2.attempts == 2
        state = await q.fail(job2.id, attempts=job2.attempts, max_attempts=2)
        assert state == "dead"  # exhausted
        assert (await q.claim()) is None
        assert (await q.stats())["dead"] == 1

    async def test_claim_is_atomic_between_workers(self, db: Database) -> None:
        """Two workers on the same table; one job is claimed exactly once."""
        q1 = DurableTaskQueue(SQLiteConnection(db.conn), "w1")
        q2 = DurableTaskQueue(SQLiteConnection(db.conn), "w2")
        await q1.enqueue(_payload())
        got1 = await q1.claim()
        got2 = await q2.claim()
        assert got1 is not None
        assert got2 is None  # pending no longer matches


class TestTaskWorker:
    async def test_worker_executes_and_completes(self, db: Database) -> None:
        calls: list[str] = []

        class _FakeOrchestrator:
            async def run(self, event: object) -> Any:
                from atlas.orchestration.types import TaskResult

                calls.append(event.content)  # type: ignore[attr-defined]
                return TaskResult(task_id="t", ok=True, answer="done")  # type: ignore[arg-type]

        conn = SQLiteConnection(db.conn)
        q = DurableTaskQueue(conn, "test")
        await q.enqueue(_payload())
        worker = TaskWorker(orchestrator=_FakeOrchestrator(), conn=conn)  # type: ignore[arg-type]

        # Run exactly one job instead of forever.
        job = await worker._queue.claim()
        assert job is not None
        await worker._execute(job)
        assert calls == ["do 1"]
        assert (await q.stats())["done"] == 1

    async def test_worker_failure_requeues(self, db: Database) -> None:
        class _BrokenOrchestrator:
            async def run(self, event: object) -> Any:
                raise RuntimeError("boom")

        conn = SQLiteConnection(db.conn)
        q = DurableTaskQueue(conn, "test")
        await q.enqueue(_payload(), max_attempts=1)
        worker = TaskWorker(orchestrator=_BrokenOrchestrator(), conn=conn)  # type: ignore[arg-type]
        job = await worker._queue.claim()
        assert job is not None
        await worker._execute(job)
        assert (await q.stats())["dead"] == 1


class TestPlaceholderTranslation:
    def test_translates_outside_strings(self) -> None:
        assert translate_placeholders("UPDATE t SET a=? WHERE b=?") == "UPDATE t SET a=$1 WHERE b=$2"

    def test_preserves_quoted(self) -> None:
        sql = "UPDATE t SET a='what?' WHERE b=?"
        assert translate_placeholders(sql) == "UPDATE t SET a='what?' WHERE b=$1"

    def test_no_placeholders(self) -> None:
        assert translate_placeholders("SELECT 1") is None


class TestResumeGating:
    def _registry(self, idempotent: bool) -> ToolRegistry:
        reg = ToolRegistry()

        class _T:
            name = "tool"

            def dry_run(self, a: dict) -> str:
                return "p"

            async def execute(self, a: dict) -> None:
                raise NotImplementedError

        reg.register(_T(), ("read",), ToolMetadata(name="tool", idempotent=idempotent))
        return reg

    def test_idempotent_plan_allowed(self) -> None:
        plan = Plan(goal="g", steps=(PlanStep(index=0, intent="i", tool="tool", operation="read"),))
        decision = assess_resume_safety(plan, self._registry(idempotent=True))
        assert decision.allowed

    def test_side_effecting_plan_refused(self) -> None:
        plan = Plan(goal="g", steps=(PlanStep(index=0, intent="i", tool="tool", operation="write"),))
        decision = assess_resume_safety(plan, self._registry(idempotent=False))
        assert not decision.allowed
        assert "side effects" in decision.reason

    def test_unknown_tool_refused(self) -> None:
        plan = Plan(goal="g", steps=(PlanStep(index=0, intent="i", tool="mystery", operation="x"),))
        decision = assess_resume_safety(plan, self._registry(idempotent=True))
        assert not decision.allowed
        assert "cannot verify" in decision.reason

    def test_reasoning_only_steps_allowed(self) -> None:
        plan = Plan(goal="g", steps=(PlanStep(index=0, intent="think"),))
        assert assess_resume_safety(plan, self._registry(idempotent=True)).allowed

    def test_plan_from_checkpoint_roundtrip(self) -> None:
        raw = {
            "goal": "g",
            "risk": "high",
            "confidence": 0.8,
            "constraints": ["be safe"],
            "steps": [
                {"index": 0, "intent": "i", "tool": "tool", "operation": "read", "args": {"x": 1}, "depends_on": []},
            ],
        }
        plan = plan_from_checkpoint(raw)
        assert plan.goal == "g" and plan.risk.value == "high"
        assert plan.steps[0].tool == "tool"
        assert plan_from_checkpoint({}).steps == ()
