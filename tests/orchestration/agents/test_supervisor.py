"""AgentSupervisor — graph execution, blast-radius containment, degradation."""

from __future__ import annotations

import asyncio

from atlas.infra.ids import CorrelationId, TaskId
from atlas.orchestration.agents.supervisor import AgentSupervisor, SupervisionResult
from atlas.orchestration.agents.synthesizer import Synthesizer
from atlas.orchestration.agents.types import (
    AgentRole,
    DecompositionOutcome,
    SubTask,
    SubTaskStatus,
    TaskDAG,
)
from atlas.orchestration.errors import CancellationError
from atlas.orchestration.managers.cancellation import CancellationToken
from tests.orchestration.agents._fakes import (
    RecordingEvents,
    ScriptedGateway,
    ScriptedSpecialist,
    intent,
)

TASK = TaskId("t1")
CORR = CorrelationId("c1")


class ScriptedDecomposer:
    """Returns a fixed outcome; records that it was consulted."""

    def __init__(self, outcome: DecompositionOutcome) -> None:
        self._outcome = outcome
        self.calls = 0

    async def decompose(self, *_args: object, **_kwargs: object) -> DecompositionOutcome:
        self.calls += 1
        return self._outcome


def st(sid: str, *deps: str, role: AgentRole = AgentRole.GENERAL) -> SubTask:
    return SubTask(id=sid, role=role, objective=f"objective {sid}", depends_on=deps)


def delegating(*subtasks: SubTask) -> DecompositionOutcome:
    return DecompositionOutcome(
        should_delegate=True,
        reason="worth splitting",
        dag=TaskDAG.build("goal", subtasks),
    )


def build(
    outcome: DecompositionOutcome,
    specialist: ScriptedSpecialist,
    *,
    events: RecordingEvents | None = None,
    synth: str = "the final answer",
    **kwargs: object,
) -> AgentSupervisor:
    return AgentSupervisor(
        decomposer=ScriptedDecomposer(outcome),  # type: ignore[arg-type]
        specialist=specialist,  # type: ignore[arg-type]
        synthesizer=Synthesizer(ScriptedGateway(synth)),  # type: ignore[arg-type]
        events=events or RecordingEvents(),  # type: ignore[arg-type]
        **kwargs,  # type: ignore[arg-type]
    )


async def run(sup: AgentSupervisor, *, token: CancellationToken | None = None) -> SupervisionResult:
    return await sup.maybe_run(
        task_id=TASK,
        correlation_id=CORR,
        request="the original request",
        context="base context",
        intent=intent(),
        token=token or CancellationToken(),
    )


async def test_declining_returns_delegated_false_and_runs_nothing() -> None:
    spec = ScriptedSpecialist()
    sup = build(DecompositionOutcome(should_delegate=False, reason="too simple"), spec)

    result = await run(sup)

    assert result.delegated is False
    assert result.reason == "too simple"
    assert spec.ran == []
    assert result.answer is None


async def test_delegating_with_an_empty_dag_still_declines() -> None:
    sup = build(
        DecompositionOutcome(should_delegate=True, reason="oops", dag=TaskDAG.build("g", ())),
        ScriptedSpecialist(),
    )

    assert (await run(sup)).delegated is False


async def test_happy_path_runs_every_subtask_and_synthesizes() -> None:
    spec = ScriptedSpecialist()
    sup = build(delegating(st("st1"), st("st2"), st("st3", "st1")), spec)

    result = await run(sup)

    assert result.delegated is True
    assert result.ok is True
    assert result.answer == "the final answer"
    assert sorted(spec.ran) == ["st1", "st2", "st3"]
    assert [r.subtask_id for r in result.results] == ["st1", "st2", "st3"]
    assert all(r.ok for r in result.results)


async def test_dependencies_are_respected_in_execution_order() -> None:
    spec = ScriptedSpecialist()
    sup = build(delegating(st("st1"), st("st2", "st1"), st("st3", "st2")), spec)

    await run(sup)

    assert spec.ran == ["st1", "st2", "st3"]


async def test_a_specialist_sees_only_its_declared_dependencies() -> None:
    spec = ScriptedSpecialist()
    sup = build(delegating(st("st1"), st("st2"), st("st3", "st1")), spec)

    await run(sup)

    assert spec.upstream_seen["st1"] == ""
    assert spec.upstream_seen["st2"] == ""  # sibling output must not leak in
    assert "output of st1" in spec.upstream_seen["st3"]
    assert "output of st2" not in spec.upstream_seen["st3"]


async def test_a_failed_subtask_skips_its_transitive_dependents() -> None:
    spec = ScriptedSpecialist({"st1": SubTaskStatus.FAILED})
    sup = build(delegating(st("st1"), st("st2", "st1"), st("st3", "st2"), st("st4")), spec)

    result = await run(sup)

    statuses = {r.subtask_id: r.status for r in result.results}
    assert statuses["st1"] is SubTaskStatus.FAILED
    assert statuses["st2"] is SubTaskStatus.SKIPPED
    assert statuses["st3"] is SubTaskStatus.SKIPPED
    assert statuses["st4"] is SubTaskStatus.SUCCEEDED  # independent branch survives
    assert "st2" not in spec.ran and "st3" not in spec.ran
    assert result.ok is True  # st4 produced something


async def test_every_subtask_gets_exactly_one_result() -> None:
    spec = ScriptedSpecialist({"st1": SubTaskStatus.FAILED})
    dag_subtasks = (st("st1"), st("st2", "st1"), st("st3", "st1"), st("st4", "st2", "st3"))
    sup = build(delegating(*dag_subtasks), spec)

    result = await run(sup)

    ids = [r.subtask_id for r in result.results]
    assert ids == ["st1", "st2", "st3", "st4"]
    assert len(ids) == len(set(ids))


async def test_all_subtasks_failing_is_not_ok() -> None:
    spec = ScriptedSpecialist({"st1": SubTaskStatus.FAILED, "st2": SubTaskStatus.FAILED})
    sup = build(delegating(st("st1"), st("st2")), spec)

    result = await run(sup)

    assert result.delegated is True
    assert result.ok is False
    assert result.summary() == "failed=2"


async def test_a_specialist_raising_cancellation_becomes_a_skip_not_a_crash() -> None:
    spec = ScriptedSpecialist(raises={"st1": CancellationError("stop")})
    sup = build(delegating(st("st1"), st("st2")), spec)

    result = await run(sup)

    statuses = {r.subtask_id: r.status for r in result.results}
    assert statuses["st1"] is SubTaskStatus.SKIPPED
    assert statuses["st2"] is SubTaskStatus.SUCCEEDED


async def test_a_pre_cancelled_token_skips_everything() -> None:
    token = CancellationToken()
    token.cancel()
    spec = ScriptedSpecialist()
    sup = build(delegating(st("st1"), st("st2")), spec)

    result = await run(sup, token=token)

    assert spec.ran == []
    assert all(r.status is SubTaskStatus.SKIPPED for r in result.results)
    assert result.ok is False


async def test_deadline_abandons_remaining_batches() -> None:
    spec = ScriptedSpecialist()
    # deadline_s=0 -> the check fires before the first batch runs.
    sup = build(delegating(st("st1"), st("st2", "st1")), spec, deadline_s=0.0)

    result = await run(sup)

    assert spec.ran == []
    assert all(r.status is SubTaskStatus.SKIPPED for r in result.results)
    assert any("deadline" in (r.error or "") for r in result.results)


async def test_concurrency_is_capped_by_the_semaphore() -> None:
    peak = 0
    live = 0

    class CountingSpecialist(ScriptedSpecialist):
        async def run(self, **kwargs: object) -> object:  # type: ignore[override]
            nonlocal peak, live
            live += 1
            peak = max(peak, live)
            await asyncio.sleep(0.01)
            live -= 1
            return await super().run(**kwargs)  # type: ignore[arg-type]

    spec = CountingSpecialist()
    sup = build(delegating(st("st1"), st("st2"), st("st3"), st("st4")), spec, max_concurrency=2)

    await run(sup)

    assert peak == 2
    assert sorted(spec.ran) == ["st1", "st2", "st3", "st4"]


async def test_independent_subtasks_actually_overlap() -> None:
    """A batch must run concurrently, not serially — that is the whole point."""
    gate = asyncio.Event()
    entered = 0

    class BlockingSpecialist(ScriptedSpecialist):
        async def run(self, **kwargs: object) -> object:  # type: ignore[override]
            nonlocal entered
            entered += 1
            if entered == 2:
                gate.set()
            await asyncio.wait_for(gate.wait(), timeout=2.0)
            return await super().run(**kwargs)  # type: ignore[arg-type]

    spec = BlockingSpecialist()
    sup = build(delegating(st("st1"), st("st2")), spec, max_concurrency=2)

    result = await run(sup)  # would time out if the batch were serial

    assert all(r.ok for r in result.results)


async def test_lifecycle_events_are_emitted_for_the_whole_run() -> None:
    events = RecordingEvents()
    spec = ScriptedSpecialist()
    sup = build(delegating(st("st1"), st("st2", "st1")), spec, events=events)

    await run(sup)

    kinds = events.kinds()
    assert kinds[0] == "agents.decomposed"
    assert kinds[-1] == "agents.synthesized"
    assert kinds.count("agents.subtask_started") == 2
    assert kinds.count("agents.subtask_finished") == 2
    decomposed = events.emitted[0]
    assert decomposed["subtasks"] == 2
    assert decomposed["batches"] == 2


async def test_synthesis_falls_back_to_a_digest_when_the_model_fails() -> None:
    spec = ScriptedSpecialist()
    sup = AgentSupervisor(
        decomposer=ScriptedDecomposer(delegating(st("st1"), st("st2"))),  # type: ignore[arg-type]
        specialist=spec,  # type: ignore[arg-type]
        synthesizer=Synthesizer(ScriptedGateway(RuntimeError("no provider"))),  # type: ignore[arg-type]
        events=RecordingEvents(),  # type: ignore[arg-type]
    )

    result = await run(sup)

    assert result.delegated is True
    assert result.answer is not None
    assert "output of st1" in result.answer  # degraded, never empty


async def test_accounting_and_trajectory_roll_up_to_the_parent() -> None:
    spec = ScriptedSpecialist()
    sup = build(delegating(st("st1"), st("st2")), spec)

    result = await run(sup)

    assert result.steps_taken == 4  # 2 per subtask
    assert result.tool_calls == 2
    assert result.model_calls == 2
    assert result.tokens_used == 200
