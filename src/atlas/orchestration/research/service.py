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

from atlas.infra.clock import Clock
from atlas.infra.ids import IdGenerator
from atlas.infra.logging import get_logger
from atlas.orchestration.agent_engine.engine import SupportsDispatch
from atlas.orchestration.research.persistence import ResearchSessionStore
from atlas.orchestration.research.records import (
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
        — never a fabricated answer (§54/§69).
        """
        cleaned = question.strip()
        if not cleaned:
            raise ResearchError("research question must not be empty")
        chosen_mode = (mode or self._default_mode).strip().lower()
        if chosen_mode not in _VALID_MODES:
            raise ResearchError(f"unknown research mode {chosen_mode!r}")

        session_id = str(self._ids.execution_id())
        cid = self._ids.correlation_id()
        goal = cleaned[: self._max_question_chars]
        now = self._clock.now().isoformat()

        observation = await self._dispatcher.dispatch(
            Action(
                step=0,
                kind="tool_call",
                tool=KNOWLEDGE_TOOL,
                operation=chosen_mode,
                # Both keys: `search` reads `query`, `research`/`deep_research` read
                # `goal` (falling back to `query`). Sending both keeps one call site.
                args={"goal": goal, "query": goal},
            ),
            cid,
        )

        if not observation.ok:
            record = ResearchSessionRecord(
                session_id=session_id,
                correlation_id=str(cid),
                parent_session_id=parent_session_id,
                question=goal,
                mode=chosen_mode,
                status=ResearchStatus.FAILED.value,
                error=observation.error or "research dispatch failed",
                created_ts=now,
                updated_ts=now,
            )
            await self._store.save_session(record)
            _log.warning(
                "research.session.failed",
                event_type="research",
                session_id=session_id,
                error=record.error,
            )
            return record

        outcome = build_result(observation.content)
        record = ResearchSessionRecord(
            session_id=session_id,
            correlation_id=str(cid),
            parent_session_id=parent_session_id,
            question=goal,
            mode=chosen_mode,
            answer=outcome.answer,
            sources=outcome.sources,
            stop_reason=outcome.stop_reason,
            total_rounds=outcome.total_rounds,
            total_discovered=outcome.total_discovered,
            open_questions=outcome.open_questions,
            rounds=outcome.rounds,
            questions=outcome.questions,
            created_ts=now,
            updated_ts=now,
        )
        await self._store.save_session(record)
        _log.info(
            "research.session.completed",
            event_type="research",
            session_id=session_id,
            status=record.status,
            answered=outcome.answer.answered,
            sources=len(outcome.sources),
        )
        return record

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

    async def get_session(self, session_id: str) -> ResearchSessionRecord | None:
        return await self._store.load_session(session_id)

    async def list_sessions(self, *, limit: int | None = None) -> tuple[ResearchSessionRecord, ...]:
        bound = min(limit or self._max_sessions_listed, self._max_sessions_listed)
        return await self._store.list_sessions(limit=bound)
