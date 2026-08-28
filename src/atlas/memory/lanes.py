"""Two-lane recall — the read path that keeps a turn fast.

The default recall design in most agent frameworks is one lane: every turn embeds
the query, hits a vector store, and often asks a model to rank the results. That
is three sources of latency on the hot path, and two of them are network calls.

WHY two lanes: the overwhelming majority of turns need either nothing from
long-term memory or something a keyword and a recency decay will find. So:

* **Lane 1** (default, every turn) — the curated tier plus one indexed SQL query
  over write-time trigger hints, ranked by ``importance * recency-decay``. Zero
  embedding calls, zero vector round-trips, zero model calls. Pure arithmetic.
* **Lane 2** (escalation, rare) — the existing hybrid vector retrieval. Only
  reached when Lane 1 finds nothing usable *and* the message actually asks for
  deep recall. Because it is rare by construction, the vector store stops being
  either a latency cost or a storage-ceiling problem.

WHY the decay is computed in Python and not in SQL: ``exp()`` is an optional
SQLite build flag, so a SQL-side decay would work on one machine and fail on
another. The arithmetic runs over the handful of rows the partial index already
narrowed to, so there is nothing to gain by pushing it down — and doing it here
means the ranking is directly unit-testable.

WHY provenance is filtered in the query and not after: an untrusted episode must
never be *considered*, let alone injected. Filtering in SQL means a caller cannot
forget.
"""

from __future__ import annotations

import math
import re
from datetime import UTC, datetime

from atlas.infra.clock import Clock
from atlas.infra.db import Database
from atlas.infra.logging import get_logger
from atlas.memory.curated import CuratedMemory
from atlas.memory.types import Episode, EpisodeKind, OriginClass, RecallHit, SessionKind

_log = get_logger("atlas.memory.lanes")

#: 30-day half-life. A month-old event is worth half a fresh one at equal
#: importance — enough to keep long-running projects alive without letting last
#: spring's decisions outrank this morning's.
_DEFAULT_HALF_LIFE_DAYS = 30.0

#: At most three recalled episodes per turn. This is a context-budget decision,
#: not a relevance one: injecting ten "relevant" memories reliably makes answers
#: worse than injecting the best three.
_DEFAULT_LIMIT = 3

#: Terms this short or this common carry no retrieval signal and would match
#: every row, defeating the index.
_MIN_TERM_LEN = 3
_STOPWORDS = frozenset(
    """
    the a an and or but for nor yet so of to in on at by with from into over
    is are was were be been being do does did doing have has had having
    i you he she it we they me him her us them my your his its our their
    this that these those there here what which who whom whose when where why how
    not no can could would should will shall may might must if then than as
    about after again all also any because before both each few more most other
    some such only own same too very just now
    """.split()
)

#: Phrasing that means "go look it up properly" rather than "answer me". Used to
#: gate Lane 2 — a cheap deterministic classifier, because deciding *whether* to
#: pay for deep recall must not itself cost a model call.
_RECALL_INTENT = re.compile(
    r"""
    \b(
        last\s+(week|month|year|time|night)
      | yesterday | earlier | previously | before
      | (what|which|when|where|why|how)\s+(did|was|were|had|have)\s+(we|you|i)
      | do\s+you\s+remember
      | remind\s+me
      | we\s+(decided|discussed|talked|agreed|said)
      | you\s+(said|told|mentioned|suggested)
      | (back|way)\s+(then|when)
      | our\s+(last|previous|earlier)
      | in\s+the\s+past
      | history | recap
    )\b
    """,
    re.IGNORECASE | re.VERBOSE,
)


def terms_of(message: str) -> tuple[str, ...]:
    """Extract the retrieval terms from an inbound message.

    Lowercased, de-duplicated, stopwords and short tokens dropped, order
    preserved so the first (usually most specific) terms stay first.
    """
    seen: dict[str, None] = {}
    for raw in re.findall(r"[a-z0-9']+", message.lower()):
        if len(raw) >= _MIN_TERM_LEN and raw not in _STOPWORDS:
            seen.setdefault(raw, None)
    return tuple(seen)


def has_recall_intent(message: str) -> bool:
    """Whether the message explicitly asks about the past.

    Deliberately conservative: a false negative just means the turn answers from
    Lane 1 and the curated tier, which is the normal, fast case. A false positive
    costs a vector query nobody asked for.
    """
    return bool(_RECALL_INTENT.search(message))


def decayed_score(*, importance: int | None, age_seconds: float, half_life_days: float) -> float:
    """``importance * 2^(-age / half_life)``.

    ``importance`` of ``None`` is treated as 1 rather than 0: an unscored episode
    that matched a trigger is weak evidence, not *no* evidence, and scoring it
    zero would make it unrankable against other unscored rows.
    """
    weight = float(importance if importance is not None else 1)
    if half_life_days <= 0:
        return weight
    half_lives = max(0.0, age_seconds) / (half_life_days * 86400.0)
    return weight * math.pow(2.0, -half_lives)


class LaneOneRecall:
    """The default read path: curated tier + one indexed SQL query."""

    def __init__(
        self,
        db: Database,
        clock: Clock,
        curated: CuratedMemory,
        *,
        half_life_days: float = _DEFAULT_HALF_LIFE_DAYS,
        limit: int = _DEFAULT_LIMIT,
    ) -> None:
        self._db = db
        self._clock = clock
        self._curated = curated
        self._half_life_days = half_life_days
        self._limit = limit

    async def bootstrap(self) -> str:
        """The always-loaded curated tier, rendered for the prompt."""
        return await self._curated.bootstrap()

    async def recall(self, message: str, *, limit: int | None = None) -> list[RecallHit]:
        """Ranked trigger-hint recall. No embeddings, no model call, no network.

        Returns at most ``limit`` hits, highest score first. An empty result is
        the signal Lane 2 gates on.
        """
        terms = terms_of(message)
        if not terms:
            return []

        # One LIKE per term against the partial-indexed hint column. Bounded by
        # construction: the term list comes from a single user message, and the
        # candidate cap keeps the Python-side scoring O(small) no matter how
        # many rows carry hints.
        where_terms = " OR ".join("lower(trigger_hint) LIKE ?" for _ in terms)
        params: list[object] = [f"%{t}%" for t in terms]
        sql = (
            "SELECT id, correlation_id, task_id, step, ts, kind, role, content, tool, outcome, "
            "       salience, tokens, origin_class, session_kind, importance, trigger_hint "
            "FROM episodes "
            "WHERE trigger_hint IS NOT NULL "
            f"  AND origin_class IN ('{OriginClass.OWNER.value}', '{OriginClass.AGENT.value}') "
            f"  AND ({where_terms}) "
            "ORDER BY ts DESC "
            "LIMIT 200"
        )
        cur = await self._db.conn.execute(sql, params)
        rows = list(await cur.fetchall())
        if not rows:
            return []

        now = self._clock.now()
        hits: list[RecallHit] = []
        for row in rows:
            episode = self._row(row)
            hint = (row["trigger_hint"] or "").lower()
            matched = tuple(t for t in terms if t in hint)
            hits.append(
                RecallHit(
                    episode=episode,
                    score=decayed_score(
                        importance=episode.importance,
                        age_seconds=self._age_seconds(now, episode.ts),
                        half_life_days=self._half_life_days,
                    )
                    # More matched terms is a stronger signal than one; a small
                    # multiplier keeps it a tiebreak rather than a second axis.
                    * (1.0 + 0.25 * (len(matched) - 1)),
                    matched_terms=matched,
                )
            )

        hits.sort(key=lambda h: h.score, reverse=True)
        capped = hits[: (limit if limit is not None else self._limit)]
        _log.debug(
            "recall.lane1",
            event_type="memory",
            candidates=len(rows),
            returned=len(capped),
            terms=len(terms),
        )
        return capped

    def should_escalate(self, message: str, hits: list[RecallHit], *, min_score: float = 0.5) -> bool:
        """Whether this turn has earned a Lane-2 vector search.

        Both conditions must hold — nothing usable from Lane 1, *and* explicit
        recall intent. Either one alone would escalate far too often: a novel
        question with no stored triggers is the common case, and "remind me" in
        passing does not justify a deep search when Lane 1 already answered.
        """
        if any(h.score >= min_score for h in hits):
            return False
        return has_recall_intent(message)

    @staticmethod
    def _age_seconds(now: datetime, ts: datetime) -> float:
        # Episodes are stored as ISO strings and may come back naive. Treat a
        # naive timestamp as UTC rather than raising — a wrong-by-an-offset
        # decay is a mild ranking error; a crash on the hot read path is not.
        left = now if now.tzinfo else now.replace(tzinfo=UTC)
        right = ts if ts.tzinfo else ts.replace(tzinfo=UTC)
        return (left - right).total_seconds()

    @staticmethod
    def _row(row: object) -> Episode:
        d = dict(row)  # type: ignore[call-overload]
        return Episode(
            id=d["id"],
            correlation_id=d["correlation_id"],
            task_id=d["task_id"],
            step=d["step"],
            ts=datetime.fromisoformat(d["ts"]),
            kind=EpisodeKind(d["kind"]),
            role=d["role"],
            content=d["content"],
            tool=d["tool"],
            outcome=d["outcome"],
            salience=d["salience"],
            tokens=d["tokens"],
            origin_class=OriginClass(d["origin_class"]),
            session_kind=SessionKind(d["session_kind"]),
            importance=d["importance"],
            trigger_hint=d["trigger_hint"],
        )
