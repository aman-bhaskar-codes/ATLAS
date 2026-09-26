"""Agent-run use-case (M2.1) — the governed engine as a live, persisted service.

WHY this module: M0.2 built the ``AgentEngine`` loop and M0.5 built the durable
``AgentRunRecord`` + rehydration substrate, but nothing composed them into a
callable use-case — the engine was an island, consumed nowhere in production. This
service is that composition and ONLY that: seed a conversation, shortlist tools
(Tool-RAG), run the governed loop, persist the run, and expose get/list/continue.

It owns no HTTP and no policy. The tools it drives flow through the SAME
``ToolDispatcher -> SafetyEngine.guard`` funnel the engine already calls and the
SAME ``ExecutionLimits`` seatbelts — so the agent surface is not a second
execution path and can never become a side door around ATLAS policy (Constitution).

Seams are structural, like the engine's: a gateway (``SupportsInfer``), a
dispatcher (``SupportsDispatch``), a ``ToolRouter`` for the shortlist, and an
``AgentRunStore`` for persistence, plus ids/clock. Tests inject fakes with no
network and no registry; production passes the real wired graph.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence

from atlas.infra.clock import Clock
from atlas.infra.ids import CorrelationId, IdGenerator
from atlas.infra.logging import get_logger
from atlas.infra.types import ToolCallSpec
from atlas.intelligence.contracts import Message
from atlas.orchestration.agent_engine.context import continue_messages, seed_messages
from atlas.orchestration.agent_engine.engine import AgentEngine, SupportsDispatch, SupportsInfer
from atlas.orchestration.agent_engine.event_reader import AgentEventReader, AgentRunEvent
from atlas.orchestration.agent_engine.persistence import AgentRunStore
from atlas.orchestration.agent_engine.records import (
    AgentEngineResult,
    AgentRunRecord,
    RunStatus,
    StopReason,
)
from atlas.orchestration.events import EventPublisher
from atlas.orchestration.limits import ExecutionLimits
from atlas.orchestration.tool_routing import ToolRouter

_log = get_logger("atlas.agent_engine.service")

DEFAULT_AGENT_SYSTEM_PROMPT = (
    "You are ATLAS, an elite, autonomous AI software engineer. You solve the "
    "user's task end to end: REASON about it, then USE YOUR TOOLS to read, search, "
    "write, and run code — never guess a file's contents or a command's output "
    "when a tool can get you the ground truth. Take real actions with the tools "
    "available; do not merely describe what you would do. When the task is complete, "
    "stop calling tools and give a concise final answer stating what you changed and "
    "how you verified it."
)


class AgentRunError(RuntimeError):
    """A use-case-level failure (e.g. continuing a run that does not exist)."""


class AgentRunNotReady(AgentRunError):  # noqa: N818 — reads as a state, not an error
    """A run exists but is not in a state the operation allows (e.g. continuing a
    run that is still ``running``). Distinct from ``AgentRunError`` so the HTTP
    surface can map it to 409 Conflict rather than 404 Not Found."""


class AgentRunService:
    """Composes the M0 engine + store into a governed, persisted agent-run surface."""

    def __init__(
        self,
        *,
        gateway: SupportsInfer,
        dispatcher: SupportsDispatch,
        tool_router: ToolRouter,
        store: AgentRunStore,
        ids: IdGenerator,
        clock: Clock,
        publisher: EventPublisher | None = None,
        event_reader: AgentEventReader | None = None,
        limits: ExecutionLimits | None = None,
        system_prompt: str = DEFAULT_AGENT_SYSTEM_PROMPT,
        max_tools: int = 8,
        max_output_tokens: int = 2048,
    ) -> None:
        self._router = tool_router
        self._store = store
        self._reader = event_reader
        self._ids = ids
        self._clock = clock
        self._system_prompt = system_prompt
        self._max_tools = max_tools
        self._max_output_tokens = max_output_tokens
        # The loop is stateless across runs (each run() builds its own LimitCounter),
        # so one engine instance is safe to reuse for every request.
        self._engine = AgentEngine(gateway=gateway, dispatcher=dispatcher, publisher=publisher, limits=limits)
        # In-flight background runs: run_id -> its executing task. A run sits here only
        # between start_run_background() and its terminal persistence; the task's
        # done-callback evicts it. Lets the surface await/report live runs (M2.2).
        self._inflight: dict[str, asyncio.Task[None]] = {}

    async def start_run(
        self,
        request: str,
        *,
        workspace_id: str | None = None,
        session_id: str | None = None,
        max_tools: int | None = None,
    ) -> AgentRunRecord:
        """Run one governed agent loop for ``request`` and persist it.

        Seeds ``system + user`` context, shortlists tools for the request's intent
        (Tool-RAG), drives the loop, and stores the resulting ``AgentRunRecord``.
        Always returns a record: the engine never raises — a limit/error terminates
        the run gracefully into a record whose ``status`` reflects the stop reason.
        """
        run_id = str(self._ids.execution_id())
        cid = self._ids.correlation_id()
        seed = seed_messages(self._system_prompt, request)
        tools = self._router.shortlist_specs(request, max_tools=max_tools or self._max_tools)
        return await self._run(
            run_id=run_id,
            correlation_id=cid,
            request=request,
            messages=seed,
            tools=tools,
            seed_for_record=seed,
            workspace_id=workspace_id,
            session_id=session_id,
            parent_run_id=None,
        )

    async def start_run_background(
        self,
        request: str,
        *,
        workspace_id: str | None = None,
        session_id: str | None = None,
        max_tools: int | None = None,
    ) -> AgentRunRecord:
        """Persist a ``running`` stub, launch the governed loop on a background task,
        and return the stub immediately — the live-console entry point (M2.2).

        The run is addressable (get/list) and streamable the instant this returns;
        its terminal record overwrites the stub when the loop finishes. Same seams
        and the SAME SafetyEngine funnel as ``start_run`` — backgrounding changes
        WHEN the work runs, never HOW it is governed.
        """
        run_id = str(self._ids.execution_id())
        cid = self._ids.correlation_id()
        seed = seed_messages(self._system_prompt, request)
        tools = self._router.shortlist_specs(request, max_tools=max_tools or self._max_tools)
        now = self._clock.now().isoformat()
        stub = AgentRunRecord(
            run_id=run_id,
            task_id=run_id,
            correlation_id=str(cid),
            workspace_id=workspace_id,
            session_id=session_id,
            parent_run_id=None,
            request=request,
            tool_names=tuple(spec.name for spec in tools),
            seed_messages=tuple(seed),
            result=None,
            status=RunStatus.RUNNING.value,
            created_ts=now,
            updated_ts=now,
        )
        await self._store.save_run(stub)
        # Governed loop runs on a detached task; the stub is already persisted, so
        # the run is addressable/streamable the instant we return. The done-callback
        # evicts the entry — _execute_and_persist guarantees a terminal record lands
        # even on crash, so a run never strands in ``running``.
        task = asyncio.create_task(
            self._execute_and_persist(
                run_id=run_id,
                correlation_id=cid,
                request=request,
                messages=seed,
                tools=tools,
                seed_for_record=seed,
                workspace_id=workspace_id,
                session_id=session_id,
                parent_run_id=None,
                created_ts=now,
            )
        )
        self._inflight[run_id] = task
        task.add_done_callback(lambda _t: self._inflight.pop(run_id, None))
        return stub

    async def continue_run(self, run_id: str, request: str, *, max_tools: int | None = None) -> AgentRunRecord:
        """Rehydrate a finished run and drive a new turn — a fresh, linked run.

        The prior run is rebuilt (M0.5 ``continue_messages``) into the exact
        conversation it produced, a new USER turn is appended, and the loop runs
        again. The new record's ``seed_messages`` is that full rehydrated history,
        so the continuation is itself self-contained and further continuable; it
        carries ``parent_run_id`` for lineage and inherits the parent's workspace/
        session binding.
        """
        prior = await self._store.load_run(run_id)
        if prior is None:
            raise AgentRunError(f"cannot continue unknown run {run_id!r}")
        if prior.result is None:
            raise AgentRunNotReady(f"cannot continue run {run_id!r} while it is still running")
        new_run_id = str(self._ids.execution_id())
        cid = self._ids.correlation_id()
        messages = continue_messages(prior.seed_messages, prior.result, request)
        tools = self._router.shortlist_specs(request, max_tools=max_tools or self._max_tools)
        return await self._run(
            run_id=new_run_id,
            correlation_id=cid,
            request=request,
            messages=messages,
            tools=tools,
            seed_for_record=messages,
            workspace_id=prior.workspace_id,
            session_id=prior.session_id,
            parent_run_id=prior.run_id,
        )

    async def get_run(self, run_id: str) -> AgentRunRecord | None:
        return await self._store.load_run(run_id)

    async def run_events(self, run_id: str, *, after_sequence: int = 0) -> tuple[AgentRunEvent, ...]:
        """Read a run's durable event trace in order, from a resumable cursor.

        The live console's data source: every lifecycle/tool event the loop emitted
        for ``run_id`` is persisted under ``causation_id = run_id`` and read back here
        (see ``event_reader``). Returns ``()`` when no reader is wired (a run store
        with no event bus) — the surface degrades to snapshots without a live trace
        rather than failing. ``after_sequence`` is the SSE cursor (a ``rowid``).
        """
        if self._reader is None:
            return ()
        return await self._reader.events_for_run(run_id, after_sequence=after_sequence)

    async def list_runs(self, *, workspace_id: str | None = None, limit: int = 50) -> tuple[AgentRunRecord, ...]:
        return await self._store.list_runs(workspace_id=workspace_id, limit=limit)

    async def runs_for_session(self, session_id: str) -> tuple[AgentRunRecord, ...]:
        return await self._store.runs_for_session(session_id)

    async def wait_for(self, run_id: str) -> AgentRunRecord | None:
        """Await a backgrounded run's convergence, then return its stored record.

        If the run is still in flight, blocks until its task finishes (its
        terminal record is persisted by then — the loop lands one, or the
        failure backstop does). If the run is unknown to the in-flight registry
        (already terminal, or never backgrounded), returns the current stored
        record immediately. Lets a caller block on a background run without
        touching the private task registry.
        """
        task = self._inflight.get(run_id)
        if task is not None:
            await task
        return await self._store.load_run(run_id)

    async def _run(
        self,
        *,
        run_id: str,
        correlation_id: CorrelationId,
        request: str,
        messages: Sequence[Message],
        tools: tuple[ToolCallSpec, ...],
        seed_for_record: Sequence[Message],
        workspace_id: str | None,
        session_id: str | None,
        parent_run_id: str | None,
        created_ts: str | None = None,
    ) -> AgentRunRecord:
        # task_id == run_id: the engine keys its lifecycle/tool events on it, so a
        # run's event stream and persisted task_events group under the run itself.
        result = await self._engine.run(
            correlation_id=correlation_id,
            messages=messages,
            tools=tools,
            task_id=run_id,
            max_tokens=self._max_output_tokens,
        )
        now = self._clock.now().isoformat()
        record = AgentRunRecord(
            run_id=run_id,
            task_id=run_id,
            correlation_id=str(correlation_id),
            workspace_id=workspace_id,
            session_id=session_id,
            parent_run_id=parent_run_id,
            request=request,
            tool_names=tuple(spec.name for spec in tools),
            seed_messages=tuple(seed_for_record),
            result=result,
            created_ts=created_ts or now,
            updated_ts=now,
        )
        await self._store.save_run(record)
        return record

    async def _execute_and_persist(
        self,
        *,
        run_id: str,
        correlation_id: CorrelationId,
        request: str,
        messages: Sequence[Message],
        tools: tuple[ToolCallSpec, ...],
        seed_for_record: Sequence[Message],
        workspace_id: str | None,
        session_id: str | None,
        parent_run_id: str | None,
        created_ts: str,
    ) -> None:
        """Drive the governed loop for a backgrounded run and persist its terminal
        record, overwriting the ``running`` stub. The engine itself never raises
        (a limit/error lands as a terminal ``StopReason``); this wrapper guards the
        pathological case — an unexpected failure OUTSIDE the loop — so a run can
        never strand in ``running`` and its live stream is guaranteed to close.
        """
        try:
            await self._run(
                run_id=run_id,
                correlation_id=correlation_id,
                request=request,
                messages=messages,
                tools=tools,
                seed_for_record=seed_for_record,
                workspace_id=workspace_id,
                session_id=session_id,
                parent_run_id=parent_run_id,
                created_ts=created_ts,
            )
        except Exception as exc:
            _log.exception("background agent run %s failed outside the loop", run_id)
            await self._persist_failure(
                run_id=run_id,
                correlation_id=correlation_id,
                request=request,
                seed_for_record=seed_for_record,
                tools=tools,
                workspace_id=workspace_id,
                session_id=session_id,
                parent_run_id=parent_run_id,
                created_ts=created_ts,
                error=str(exc),
            )

    async def _persist_failure(
        self,
        *,
        run_id: str,
        correlation_id: CorrelationId,
        request: str,
        seed_for_record: Sequence[Message],
        tools: tuple[ToolCallSpec, ...],
        workspace_id: str | None,
        session_id: str | None,
        parent_run_id: str | None,
        created_ts: str,
        error: str,
    ) -> None:
        """Overwrite a stranded ``running`` stub with an ERROR terminal record.

        The convergence backstop for ``_execute_and_persist``: a run whose loop
        crashed before producing a result still lands a terminal record, so
        ``status`` leaves ``running`` and the live stream closes.
        """
        now = self._clock.now().isoformat()
        record = AgentRunRecord(
            run_id=run_id,
            task_id=run_id,
            correlation_id=str(correlation_id),
            workspace_id=workspace_id,
            session_id=session_id,
            parent_run_id=parent_run_id,
            request=request,
            tool_names=tuple(spec.name for spec in tools),
            seed_messages=tuple(seed_for_record),
            result=AgentEngineResult(ok=False, stop_reason=StopReason.ERROR, error=error),
            created_ts=created_ts,
            updated_ts=now,
        )
        await self._store.save_run(record)
