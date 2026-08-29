"""Research supervisor — the multi-round governor above one bounded round (§44, §81).

WHY this exists: `ResearchRunner.start()` executes exactly ONE bounded research
round — it processes the open question list once against the index (fanning out to
live discovery when wired), records facts, and stops the instant a *within-round*
condition trips (per-round page/query/time budget, no open questions left, or a
low-gain streak inside the round). A single round rarely closes a hard goal: the
first round warms a cold index, the second reads what the first discovered, the
third fills the gaps. Nothing today drives that loop, enforces a budget ACROSS
rounds, or decides — deterministically — when repeating is no longer worth it.

`ResearchSupervisor` is that missing outer control loop. It repeatedly calls
`runner.start(goal, resume=True)` — reusing the runner's own §137 continuation, so
each round resumes the persisted session and skips every source already visited —
and adjudicates progress BETWEEN rounds:

  * goal covered      — no open questions remain after a round (clean success);
  * stalled           — a round added no new documents AND discovered nothing new
                        (the index is exhausted; resuming again would repeat);
  * diminishing return— round-mean information gain stays below STOP_GAIN for
                        STOP_STREAK consecutive rounds;
  * round budget      — max_rounds reached;
  * time budget       — cumulative wall-time across rounds exhausted.

Boundaries this deliberately does NOT cross (extend, never duplicate — §14):
  * within-round question selection, discovery fan-out, graph updates and the
    per-round stop all stay in `ResearchRunner`/`ResearchPlanner`;
  * the supervisor never re-implements `information_gain` or per-round budgeting —
    it consumes the gains the runner already computed and reuses `ResearchBudget`
    as the per-round budget and the `STOP_GAIN`/`STOP_STREAK` constants;
  * it holds no store, no vector, no retriever — it is pure round orchestration.

Determinism (§41-43): no LLM, no averaging away of signal. Same corpus + same goal
+ same budget in → same `SupervisedOutcome` out. A round that finds nothing is
reported as finding nothing (§22) — the supervisor never implies coverage a run
did not achieve.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Protocol

from atlas.infra.logging import get_logger
from atlas.knowledge.domain import ResearchQuestionStatus
from atlas.knowledge.research import (
    STOP_GAIN,
    STOP_STREAK,
    ResearchBudget,
    ResearchOutcome,
    ResearchSession,
)
from atlas.knowledge.retrieval import Candidate

_log = get_logger("atlas.knowledge.supervisor")

MAX_ROUNDS = 5  # hard ceiling on how many times a goal is re-investigated


class _RoundRunner(Protocol):
    """One bounded research round. `ResearchRunner.start` satisfies this structurally,
    so the supervisor stays testable with a fake and never reaches into runner state."""

    async def start(
        self,
        goal: str,
        *,
        resume: bool = True,
        budget: ResearchBudget | None = None,
        rewrites: tuple[str, ...] = (),
    ) -> ResearchOutcome: ...


@dataclass(frozen=True)
class SupervisorBudget:
    """Cross-round budget. `round_budget` bounds EACH round (pages/queries/time);
    `max_rounds` and `max_total_seconds` bound the loop itself."""

    max_rounds: int = MAX_ROUNDS
    max_total_seconds: float = 600.0
    round_budget: ResearchBudget = field(default_factory=ResearchBudget.default)

    @staticmethod
    def default() -> SupervisorBudget:
        return SupervisorBudget()


@dataclass(frozen=True)
class RoundRecord:
    """Honest per-round trace (§22) — what one round of the loop actually did."""

    index: int
    session_id: str
    stop_reason: str
    discovered: int
    new_documents: int
    facts: int
    mean_gain: float
    candidates: int


@dataclass(frozen=True)
class SupervisedOutcome:
    """The aggregate of a supervised, multi-round investigation."""

    goal: str
    session: ResearchSession  # the final (most-resumed) session
    rounds: tuple[RoundRecord, ...]
    candidates: tuple[Candidate, ...]  # deduped union of findings across rounds
    stop_reason: str
    total_discovered: int
    total_rounds: int

    @property
    def open_questions(self) -> int:
        return sum(1 for q in self.session.questions if q.status is ResearchQuestionStatus.OPEN)

    @property
    def summary(self) -> str:
        return (
            f"{self.total_rounds} round(s), {len(self.candidates)} finding(s), "
            f"{self.total_discovered} discovered, {self.open_questions} open question(s) "
            f"— stopped: {self.stop_reason}"
        )


@dataclass(frozen=True)
class _RoundStop:
    stop: bool
    reason: str = ""


class ResearchSupervisor:
    """Drives repeated `ResearchRunner` rounds until a global stop condition (§44, §81).

    Holds only the round runner and a clock — no store, no index — so a supervised
    run can never drift from what the rounds actually persisted.
    """

    def __init__(self, runner: _RoundRunner, *, budget: SupervisorBudget | None = None) -> None:
        self._runner = runner
        self._budget = budget or SupervisorBudget.default()

    async def investigate(
        self,
        goal: str,
        *,
        budget: SupervisorBudget | None = None,
        rewrites: tuple[str, ...] = (),
        resume: bool = True,
    ) -> SupervisedOutcome:
        """Investigate `goal` over multiple bounded rounds until a global stop.

        The first round honors the caller's `resume`; every later round resumes the
        session the previous round persisted (§137), so work compounds instead of
        repeating. Returns an honest aggregate even when zero rounds find anything.
        """
        if not goal.strip():
            raise ValueError("investigate requires a non-empty goal")
        budget = budget or self._budget
        started = time.monotonic()
        records: list[RoundRecord] = []
        merged: dict[str, Candidate] = {}
        total_discovered = 0
        low_gain_streak = 0
        prev_doc_count = 0
        last_session: ResearchSession | None = None
        stop_reason = ""

        for i in range(max(1, budget.max_rounds)):
            outcome = await self._runner.start(
                goal,
                resume=resume if i == 0 else True,
                budget=budget.round_budget,
                rewrites=rewrites,
            )
            last_session = outcome.session
            for c in outcome.candidates:
                merged.setdefault(c.chunk.chunk_id, c)
            total_discovered += outcome.discovered

            doc_count = len(outcome.session.document_ids)
            new_docs = max(0, doc_count - prev_doc_count)
            mean_gain = round(sum(outcome.gains) / len(outcome.gains), 3) if outcome.gains else 0.0
            low_gain_streak = low_gain_streak + 1 if mean_gain < STOP_GAIN else 0
            records.append(
                RoundRecord(
                    index=i,
                    session_id=outcome.session.session_id,
                    stop_reason=outcome.stop_reason,
                    discovered=outcome.discovered,
                    new_documents=new_docs,
                    facts=len(outcome.session.graph.facts()),
                    mean_gain=mean_gain,
                    candidates=len(outcome.candidates),
                )
            )

            decision = self._adjudicate(
                outcome,
                new_docs=new_docs,
                low_gain_streak=low_gain_streak,
                elapsed=time.monotonic() - started,
                max_total_seconds=budget.max_total_seconds,
            )
            if decision.stop:
                stop_reason = decision.reason
                break
            prev_doc_count = doc_count
        else:
            stop_reason = f"round budget exhausted ({budget.max_rounds} rounds)"

        assert last_session is not None  # loop runs at least once
        _log.info(
            "research.supervised",
            event_type="knowledge",
            goal=goal[:80],
            rounds=len(records),
            findings=len(merged),
            discovered=total_discovered,
            stop=stop_reason,
        )
        return SupervisedOutcome(
            goal=goal,
            session=last_session,
            rounds=tuple(records),
            candidates=tuple(merged.values()),
            stop_reason=stop_reason,
            total_discovered=total_discovered,
            total_rounds=len(records),
        )

    def _adjudicate(
        self,
        outcome: ResearchOutcome,
        *,
        new_docs: int,
        low_gain_streak: int,
        elapsed: float,
        max_total_seconds: float,
    ) -> _RoundStop:
        """Decide whether to run another round. Deterministic, ordered by finality.

        NB: this is CROSS-round logic that does not exist elsewhere — it is not a
        copy of `ResearchPlanner.should_stop`, which adjudicates WITHIN a round on a
        session whose per-round counters reset every resume. The two operate at
        different granularities on purpose.
        """
        # Goal covered: nothing left to reopen on the next resume — clean success.
        if not any(q.status is ResearchQuestionStatus.OPEN for q in outcome.session.questions):
            return _RoundStop(True, "all questions answered")
        # Time ceiling across the whole investigation.
        if elapsed >= max_total_seconds:
            return _RoundStop(True, "total time budget exhausted")
        # Stalled: the round neither discovered anything new nor found a new source,
        # so resuming again would only re-read the same index.
        if outcome.discovered == 0 and new_docs == 0:
            return _RoundStop(True, "no new sources found")
        # Diminishing returns across rounds (reuses the round-level thresholds).
        if low_gain_streak >= STOP_STREAK:
            return _RoundStop(True, f"information gain below {STOP_GAIN} for {low_gain_streak} rounds")
        return _RoundStop(False)
