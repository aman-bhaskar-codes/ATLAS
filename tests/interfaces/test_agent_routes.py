"""Agent-run REST surface — thin projection over AgentRunService (M2.1 + M2.2).

A fake service (no engine, no store, no network) wired onto ``app.state.atlas`` as
a SimpleNamespace — the routes touch only ``atlas.agent_engine``, so a full Atlas
build is unnecessary. The point is the HTTP SEAM, not the loop (locked in
tests/orchestration/test_agent_run_service.py):
  * a disabled subsystem (agent_engine=None) is 503; an empty request is 400;
  * a synchronous run returns its full trace; a ``background`` run returns a
    ``running`` stub whose ``result`` serializes as null (the M2.2 regression guard);
  * continuing an unknown run is 404; continuing a still-running run is 409.
"""

from __future__ import annotations

from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from atlas.interfaces.api.dependencies import get_atlas
from atlas.interfaces.api.routes_agent import router as agent_router
from atlas.orchestration.agent_engine.event_reader import AgentRunEvent
from atlas.orchestration.agent_engine.records import AgentEngineResult, AgentRunRecord, StopReason
from atlas.orchestration.agent_engine.service import AgentRunError, AgentRunNotReady

BASE = "/api/v1/agent"

# A canned run trace the fake service streams — ``run_events`` honors the SSE cursor
# (``after_sequence``) so the resume test exercises real cursor semantics.
_TRACE = (
    AgentRunEvent(sequence=1, type="orchestrator", causation_id="run-x", payload={"kind": "agent.started"}),
    AgentRunEvent(sequence=2, type="tool", causation_id="run-x", payload={"kind": "tool.completed", "tool": "search"}),
    AgentRunEvent(sequence=3, type="orchestrator", causation_id="run-x", payload={"kind": "agent.completed"}),
)


def _terminal(run_id: str, request: str, *, workspace_id: str | None = None) -> AgentRunRecord:
    return AgentRunRecord(
        run_id=run_id,
        task_id=run_id,
        correlation_id="cid",
        workspace_id=workspace_id,
        request=request,
        tool_names=("search",),
        result=AgentEngineResult(
            ok=True, stop_reason=StopReason.FINISHED, final_text="done", model_calls=2, tool_calls=1
        ),
        created_ts="2026-09-25T00:00:00+00:00",
        updated_ts="2026-09-25T00:00:01+00:00",
    )


def _running(run_id: str, request: str) -> AgentRunRecord:
    return AgentRunRecord(
        run_id=run_id,
        task_id=run_id,
        correlation_id="cid",
        request=request,
        tool_names=("search",),
        result=None,
        status="running",
        created_ts="2026-09-25T00:00:00+00:00",
        updated_ts="2026-09-25T00:00:00+00:00",
    )


# <<APPEND-MARKER>>
class FakeAgentService:
    """Records the seam calls the routes make and returns canned records.

    ``continue_run`` keys its behaviour off the run id so a single fake exercises
    every HTTP mapping: ``"running-run"`` → 409, ``"unknown"`` → 404, else a run.
    """

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    async def start_run(
        self,
        request: str,
        *,
        workspace_id: str | None = None,
        session_id: str | None = None,
        max_tools: int | None = None,
    ) -> AgentRunRecord:
        self.calls.append(("start_run", request))
        return _terminal("run-sync", request, workspace_id=workspace_id)

    async def start_run_background(
        self,
        request: str,
        *,
        workspace_id: str | None = None,
        session_id: str | None = None,
        max_tools: int | None = None,
    ) -> AgentRunRecord:
        self.calls.append(("start_run_background", request))
        return _running("run-bg", request)

    async def continue_run(self, run_id: str, request: str, *, max_tools: int | None = None) -> AgentRunRecord:
        self.calls.append(("continue_run", run_id))
        if run_id == "running-run":
            raise AgentRunNotReady(f"cannot continue run {run_id!r} while it is still running")
        if run_id == "unknown":
            raise AgentRunError(f"cannot continue unknown run {run_id!r}")
        return _terminal("run-cont", request)

    async def get_run(self, run_id: str) -> AgentRunRecord | None:
        return None if run_id == "missing" else _terminal(run_id, "prior")

    async def run_events(self, run_id: str, *, after_sequence: int = 0) -> tuple[AgentRunEvent, ...]:
        return tuple(e for e in _TRACE if e.sequence > after_sequence)

    async def list_runs(self, *, workspace_id: str | None = None, limit: int = 50) -> tuple[AgentRunRecord, ...]:
        return (_terminal("run-sync", "a"), _running("run-bg", "b"))


def _client(*, service: object | None) -> TestClient:
    app = FastAPI()
    app.include_router(agent_router, prefix="")  # router carries its own /api/v1/agent prefix
    app.dependency_overrides[get_atlas] = lambda: SimpleNamespace(agent_engine=service)
    return TestClient(app)


def test_disabled_subsystem_is_503() -> None:
    resp = _client(service=None).post(f"{BASE}/runs", json={"request": "hi"})
    assert resp.status_code == 503
    assert "disabled" in resp.json()["detail"]


def test_empty_request_is_400() -> None:
    resp = _client(service=FakeAgentService()).post(f"{BASE}/runs", json={"request": "   "})
    assert resp.status_code == 400


def test_synchronous_run_returns_full_trace() -> None:
    svc = FakeAgentService()
    resp = _client(service=svc).post(f"{BASE}/runs", json={"request": "do it", "workspace_id": "w1"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "finished"
    assert body["result"]["final_text"] == "done"
    assert ("start_run", "do it") in svc.calls


def test_background_run_returns_running_stub_with_null_result() -> None:
    svc = FakeAgentService()
    resp = _client(service=svc).post(f"{BASE}/runs", json={"request": "async please", "background": True})
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "running"
    assert body["result"] is None  # M2.2: a running stub serializes with a null trace
    assert ("start_run_background", "async please") in svc.calls


def test_list_tolerates_running_stub_rows() -> None:
    body = _client(service=FakeAgentService()).get(f"{BASE}/runs").json()
    rows = {r["run_id"]: r for r in body["runs"]}
    assert rows["run-sync"]["model_calls"] == 2 and rows["run-sync"]["final_text"] == "done"
    # the running row's trace fields degrade to zeros/empty rather than 500-ing
    assert rows["run-bg"]["model_calls"] == 0 and rows["run-bg"]["final_text"] == ""


def test_continue_running_run_is_409() -> None:
    resp = _client(service=FakeAgentService()).post(f"{BASE}/runs/running-run/continue", json={"request": "more"})
    assert resp.status_code == 409


def test_continue_unknown_run_is_404() -> None:
    resp = _client(service=FakeAgentService()).post(f"{BASE}/runs/unknown/continue", json={"request": "more"})
    assert resp.status_code == 404


def test_stream_emits_trace_then_closes() -> None:
    resp = _client(service=FakeAgentService()).get(f"{BASE}/runs/run-x/stream")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")
    body = resp.text
    # connected preamble, every trace event as an id-tagged agent_event, terminal close.
    assert "event: connected" in body
    assert "id: 1\nevent: agent_event" in body
    assert "id: 3\nevent: agent_event" in body
    assert '"kind":"agent.started"' in body and '"kind":"agent.completed"' in body
    assert "event: stream_closed" in body and '"status": "finished"' in body


def test_stream_unknown_run_is_404() -> None:
    resp = _client(service=FakeAgentService()).get(f"{BASE}/runs/missing/stream")
    assert resp.status_code == 404


def test_stream_resumes_from_last_event_id() -> None:
    resp = _client(service=FakeAgentService()).get(f"{BASE}/runs/run-x/stream", headers={"Last-Event-ID": "2"})
    assert resp.status_code == 200
    body = resp.text
    # only events past the cursor are replayed; the earlier ones are not.
    assert "id: 1\n" not in body and "id: 2\n" not in body
    assert "id: 3\nevent: agent_event" in body
    assert "event: stream_closed" in body
