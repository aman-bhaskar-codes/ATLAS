"""Research use-case (Phase 1, Slice R1) — the governed knowledge pipeline as a
live, persisted research service.

WHY this module: the knowledge fabric, research runner, and supervisor exist and
are reachable through the governed ``knowledge`` tool, but nothing composed them
into a persisted, addressable research SESSION — ask a question, get a grounded,
cited answer + source rail, keep it. This service is that composition and ONLY
that: dispatch one governed research operation, project its observation into a
typed ``ResearchOutcome``, persist a ``ResearchSessionRecord``, and expose
get/list.

It owns no HTTP and no policy, and — critically — no retrieval and no safety of
its own. Every research action flows through the SAME ``ToolDispatcher ->
SafetyEngine.guard`` funnel every other dispatch uses, driving the SAME
``knowledge`` tool over the SAME knowledge fabric. The research surface is
therefore not a second execution path and not a second RAG system; it can never
become a side door around ATLAS policy (Constitution).

Seams are structural, like the agent surface's: a dispatcher (``SupportsDispatch``)
and a ``ResearchSessionStore``, plus ids/clock. Tests inject a fake dispatcher with
no network and no fabric; production passes the real wired graph.
"""

from __future__ import annotations

import asyncio

from atlas.infra.clock import Clock
from atlas.infra.ids import CorrelationId, IdGenerator
from atlas.infra.logging import get_logger
from atlas.orchestration.agent_engine.engine import SupportsDispatch
from atlas.orchestration.research.persistence import ResearchSessionStore
from atlas.orchestration.research.records import (
    ResearchEvent,
    ResearchPhase,
    ResearchSessionRecord,
    ResearchStatus,
    build_result,
)
from atlas.orchestration.types import Action

_log = get_logger("atlas.research.service")

# The governed tool the service drives. `name = "knowledge"` matches the safety
# manifest seat `config/permissions.yaml` reserves — dispatching it re-enters the
# SAME funnel, so no new trust surface is invented.
KNOWLEDGE_TOOL = "knowledge"

# Research operations the surface may drive, in ascending depth. All are read/
# search operations the manifest classifies AUTO; the service never drives the
# mutating `forget` op. `search` is one fabric query; `research` one bounded round;
# `deep_research` a multi-round supervised investigation.
_VALID_MODES = frozenset({"search", "research", "deep_research"})
DEFAULT_MODE = "deep_research"


class ResearchError(RuntimeError):
    """A use-case-level failure (e.g. an unknown session, an invalid mode)."""


class ResearchService:
    """Composes the governed ``knowledge`` tool + a session store into a persisted,
    grounded research surface."""

    def __init__(
        self,
        *,
        dispatcher: SupportsDispatch,
        store: ResearchSessionStore,
        ids: IdGenerator,
        clock: Clock,
        default_mode: str = DEFAULT_MODE,
        max_question_chars: int = 4000,
        max_sessions_listed: int = 50,
    ) -> None:
        self._dispatcher = dispatcher
        self._store = store
        self._ids = ids
        self._clock = clock
        self._default_mode = default_mode if default_mode in _VALID_MODES else DEFAULT_MODE
        self._max_question_chars = max_question_chars
        self._max_sessions_listed = max_sessions_listed
        # In-flight background runs: session_id -> executing task. A session sits here
        # only between start_session_background() and its terminal persistence; the
        # done-callback evicts it. Lets the surface await/report live runs (Slice R3).
        self._inflight: dict[str, asyncio.Task[None]] = {}

    def _validate(self, question: str, mode: str | None) -> tuple[str, str]:
        """Clean + bound the question and resolve the mode, or raise ``ResearchError``."""
        cleaned = question.strip()
        if not cleaned:
            raise ResearchError("research question must not be empty")
        chosen_mode = (mode or self._default_mode).strip().lower()
        if chosen_mode not in _VALID_MODES:
            raise ResearchError(f"unknown research mode {chosen_mode!r}")
        return cleaned[: self._max_question_chars], chosen_mode

    async def _emit(self, session_id: str, phase: str, **payload: object) -> ResearchEvent:
        """Append one REAL phase event to the durable trace (Slice R3).

        Every event has a real backend origin — emitted only when the run actually
        reaches that state, never as a fabricated token stream (§69). The store
        assigns the monotonic ``sequence`` used as the SSE cursor / ``Last-Event-ID``.
        """
        return await self._store.append_event(
            session_id, phase, dict(payload), ts=self._clock.now().isoformat()
        )

    async def start_session(
        self,
        question: str,
        *,
        mode: str | None = None,
        parent_session_id: str | None = None,
    ) -> ResearchSessionRecord:
        """Run one governed research operation for ``question`` and persist it.

        Dispatches the ``knowledge`` tool (through the SafetyEngine funnel), projects
        the observation into a typed grounded answer + source rail, and stores the
        ``ResearchSessionRecord``. Always returns a record: a denied/halted dispatch
        or a tool error lands as a ``FAILED`` record carrying the error, an
        evidence-less refusal as ``REFUSED``, an answered question as ``COMPLETED``
        — never a fabricated answer (§54/§69). Emits the same durable phase trace the
        streamed background path does, so a synchronous session is replayable too.
        """
        goal, chosen_mode = self._validate(question, mode)
        session_id = str(self._ids.execution_id())
        cid = self._ids.correlation_id()
        now = self._clock.now().isoformat()
        return await self._run_research(
            session_id=session_id,
            correlation_id=cid,
            goal=goal,
            mode=chosen_mode,
            parent_session_id=parent_session_id,
            created_ts=now,
        )

    async def start_session_background(
        self,
        question: str,
        *,
        mode: str | None = None,
        parent_session_id: str | None = None,
    ) -> ResearchSessionRecord:
        """Persist a ``running`` stub, launch the governed research on a background
        task, and return the stub immediately — the streaming entry point (Slice R3).

        The session is addressable (get/list) and streamable the instant this returns;
        its terminal record overwrites the stub when the run finishes. Same governed
        ``knowledge`` dispatch and the SAME SafetyEngine funnel as ``start_session`` —
        backgrounding changes WHEN the work runs, never HOW it is governed.
        """
        goal, chosen_mode = self._validate(question, mode)
        session_id = str(self._ids.execution_id())
        cid = self._ids.correlation_id()
        now = self._clock.now().isoformat()
        stub = ResearchSessionRecord(
            session_id=session_id,
            correlation_id=str(cid),
            parent_session_id=parent_session_id,
            question=goal,
            mode=chosen_mode,
            status=ResearchStatus.RUNNING.value,
            created_ts=now,
            updated_ts=now,
        )
        await self._store.save_session(stub)
        await self._emit(session_id, ResearchPhase.STARTED.value, mode=chosen_mode)
        task = asyncio.create_task(
            self._execute_and_persist(
                session_id=session_id,
                correlation_id=cid,
                goal=goal,
                mode=chosen_mode,
                parent_session_id=parent_session_id,
                created_ts=now,
            )
        )
        self._inflight[session_id] = task
        task.add_done_callback(lambda _t: self._inflight.pop(session_id, None))
        return stub

    async def _run_research(
        self,
        *,
        session_id: str,
        correlation_id: CorrelationId,
        goal: str,
        mode: str,
        parent_session_id: str | None,
        created_ts: str,
    ) -> ResearchSessionRecord:
        """Drive ONE governed ``knowledge`` dispatch, emit the REAL phase trace derived
        from its observation, persist the terminal record, and return it.

        ``retrieving`` is emitted live around the single governed dispatch; every phase
        after it is DERIVED from the actual observation (real supervisor rounds, real
        source count, real answered/confidence/citations) — no fabricated stream (§69).
        Exactly one terminal phase (``completed``/``refused``/``failed``) closes the trace.
        """
        await self._emit(session_id, ResearchPhase.RETRIEVING.value, mode=mode)
        observation = await self._dispatcher.dispatch(
            Action(
                step=0,
                kind="tool_call",
                tool=KNOWLEDGE_TOOL,
                operation=mode,
                # Both keys: `search` reads `query`, `research`/`deep_research` read
                # `goal` (falling back to `query`). Sending both keeps one call site.
                args={"goal": goal, "query": goal},
            ),
            correlation_id,
        )
        now = self._clock.now().isoformat()

        if not observation.ok:
            error = observation.error or "research dispatch failed"
            record = ResearchSessionRecord(
                session_id=session_id,
                correlation_id=str(correlation_id),
                parent_session_id=parent_session_id,
                question=goal,
                mode=mode,
                status=ResearchStatus.FAILED.value,
                error=error,
                created_ts=created_ts,
                updated_ts=now,
            )
            await self._store.save_session(record)
            await self._emit(session_id, ResearchPhase.FAILED.value, error=error)
            _log.warning("research.session.failed", event_type="research", session_id=session_id, error=error)
            return record

        outcome = build_result(observation.content)
        # REAL derived phases — each mirrors a fact the fabric actually produced.
        for r in outcome.rounds:
            await self._emit(
                session_id,
                ResearchPhase.ROUND.value,
                index=int(r.get("index", 0) or 0),
                discovered=int(r.get("discovered", 0) or 0),
                stop_reason=str(r.get("stop_reason", "") or ""),
            )
        await self._emit(session_id, ResearchPhase.SOURCES_FOUND.value, count=len(outcome.sources))
        await self._emit(session_id, ResearchPhase.SYNTHESIZING.value)
        await self._emit(session_id, ResearchPhase.ANSWER.value, answered=outcome.answer.answered)
        await self._emit(session_id, ResearchPhase.CITATIONS.value, count=len(outcome.answer.citations))
        await self._emit(
            session_id,
            ResearchPhase.GROUNDING.value,
            answered=outcome.answer.answered,
            confidence=outcome.answer.confidence,
        )

        record = ResearchSessionRecord(
            session_id=session_id,
            correlation_id=str(correlation_id),
            parent_session_id=parent_session_id,
            question=goal,
            mode=mode,
            answer=outcome.answer,
            sources=outcome.sources,
            stop_reason=outcome.stop_reason,
            total_rounds=outcome.total_rounds,
            total_discovered=outcome.total_discovered,
            open_questions=outcome.open_questions,
            rounds=outcome.rounds,
            questions=outcome.questions,
            created_ts=created_ts,
            updated_ts=now,
        )
        await self._store.save_session(record)
        # The terminal phase mirrors the derived status exactly — one closes the trace.
        terminal = (
            ResearchPhase.COMPLETED.value
            if record.status == ResearchStatus.COMPLETED.value
            else ResearchPhase.REFUSED.value
        )
        await self._emit(session_id, terminal, status=record.status)
        _log.info(
            "research.session.completed",
            event_type="research",
            session_id=session_id,
            status=record.status,
            answered=outcome.answer.answered,
            sources=len(outcome.sources),
        )
        return record

    async def _execute_and_persist(
        self,
        *,
        session_id: str,
        correlation_id: CorrelationId,
        goal: str,
        mode: str,
        parent_session_id: str | None,
        created_ts: str,
    ) -> None:
        """Drive the governed research for a backgrounded session and persist its
        terminal record, overwriting the ``running`` stub. ``_run_research`` already
        lands a terminal record for a denied/errored dispatch; this wrapper guards the
        pathological case — a failure OUTSIDE the dispatch — so a session can never
        strand in ``running`` and its live stream is guaranteed to close.
        """
        try:
            await self._run_research(
                session_id=session_id,
                correlation_id=correlation_id,
                goal=goal,
                mode=mode,
                parent_session_id=parent_session_id,
                created_ts=created_ts,
            )
        except Exception as exc:
            _log.exception("background research session %s failed outside the dispatch", session_id)
            await self._persist_failure(
                session_id=session_id,
                correlation_id=correlation_id,
                goal=goal,
                mode=mode,
                parent_session_id=parent_session_id,
                created_ts=created_ts,
                error=str(exc),
            )

    async def _persist_failure(
        self,
        *,
        session_id: str,
        correlation_id: CorrelationId,
        goal: str,
        mode: str,
        parent_session_id: str | None,
        created_ts: str,
        error: str,
    ) -> None:
        """Overwrite a stranded ``running`` stub with a ``FAILED`` terminal record and
        close the trace — the convergence backstop for ``_execute_and_persist``."""
        now = self._clock.now().isoformat()
        record = ResearchSessionRecord(
            session_id=session_id,
            correlation_id=str(correlation_id),
            parent_session_id=parent_session_id,
            question=goal,
            mode=mode,
            status=ResearchStatus.FAILED.value,
            error=error,
            created_ts=created_ts,
            updated_ts=now,
        )
        await self._store.save_session(record)
        await self._emit(session_id, ResearchPhase.FAILED.value, error=error)

    async def follow_up(self, session_id: str, question: str, *, mode: str | None = None) -> ResearchSessionRecord:
        """Ask a follow-up against an existing session — a fresh, linked session.

        The prior session must exist; the follow-up mints a new session carrying
        ``parent_session_id`` for thread lineage and inheriting the prior mode
        unless overridden. Each follow-up is itself a governed dispatch and its own
        persisted record (Slice R2 wires the HTTP surface for this).
        """
        prior = await self._store.load_session(session_id)
        if prior is None:
            raise ResearchError(f"cannot follow up unknown research session {session_id!r}")
        return await self.start_session(question, mode=mode or prior.mode, parent_session_id=prior.session_id)

    async def follow_up_background(
        self, session_id: str, question: str, *, mode: str | None = None
    ) -> ResearchSessionRecord:
        """Background twin of ``follow_up`` — a linked, streamed continuation session."""
        prior = await self._store.load_session(session_id)
        if prior is None:
            raise ResearchError(f"cannot follow up unknown research session {session_id!r}")
        return await self.start_session_background(
            question, mode=mode or prior.mode, parent_session_id=prior.session_id
        )

    async def get_session(self, session_id: str) -> ResearchSessionRecord | None:
        return await self._store.load_session(session_id)

    async def session_events(self, session_id: str, *, after_sequence: int = 0) -> tuple[ResearchEvent, ...]:
        """Read a session's durable phase trace in order, from a resumable cursor.

        The stream's data source: every REAL phase event emitted for ``session_id`` is
        persisted with a monotonic ``sequence`` and read back here. ``after_sequence``
        is the SSE cursor (the ``Last-Event-ID``)."""
        return await self._store.list_events(session_id, after_sequence=after_sequence)

    async def wait_for(self, session_id: str) -> ResearchSessionRecord | None:
        """Await a backgrounded session's convergence, then return its stored record.

        If the run is still in flight, blocks until its task finishes (a terminal
        record is persisted by then — ``_run_research`` lands one, or the failure
        backstop does). If unknown to the in-flight registry (already terminal, or
        never backgrounded), returns the current stored record immediately."""
        task = self._inflight.get(session_id)
        if task is not None:
            await task
        return await self._store.load_session(session_id)

    async def list_sessions(self, *, limit: int | None = None) -> tuple[ResearchSessionRecord, ...]:
        bound = min(limit or self._max_sessions_listed, self._max_sessions_listed)
        return await self._store.list_sessions(limit=bound)
