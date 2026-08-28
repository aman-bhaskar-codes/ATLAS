"""Prospective memory — "remember to do X the next time Y comes up".

WHY this is a table and not a line in the curated document: a standing intent has
to be checked against *every* inbound message. If it lived in prose, checking it
would mean a model call per turn, and the model would be the thing deciding
whether it fired — which makes firing unpredictable and unauditable. As rows with
keywords, matching is a bounded deterministic scan you can unit-test.

WHY ``fire_budget`` and ``cooldown_until`` exist: the failure mode of prospective
memory is not forgetting, it is attaching itself to every turn forever. An intent
that has fired three times has either been acted on or is mismatched; either way
it should stop volunteering. The budget makes that automatic instead of relying on
someone remembering to cancel it.

WHY expiry is swept rather than checked only on read: an expired intent should
stop matching immediately, so reads filter on time *and* a sweep settles the
stored status — the read is the correctness guarantee, the sweep keeps the table
honest for anyone inspecting it.
"""

from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timedelta

from atlas.infra.clock import Clock
from atlas.infra.db import Database
from atlas.infra.logging import get_logger
from atlas.memory.types import IntentStatus, StandingIntent

_log = get_logger("atlas.memory.intents")

#: Statuses that still participate in matching.
_ACTIVE = (IntentStatus.PENDING.value, IntentStatus.ARMED.value)

#: Default quiet period after a fire. Long enough that a follow-up message in the
#: same exchange does not re-surface the same reminder.
_DEFAULT_COOLDOWN_S = 900

_SELECT = (
    "SELECT id, description, keywords, channel_scope, sender_scope, status, "
    "       fire_budget, fire_count, cooldown_until, expires_at, created_ts, updated_ts "
    "FROM standing_intents "
)


def keyword_matches(keyword: str, message: str) -> bool:
    """Whether ``keyword`` occurs in ``message`` on word boundaries.

    Word-bounded rather than plain substring so a keyword like ``"art"`` does not
    fire on ``"start"``. Multi-word keywords work unchanged because the boundary
    anchors sit at the ends of the whole phrase.

    WHY the anchors are conditional: ``\\b`` asserts a word/non-word transition,
    so pinning one after a keyword that *ends* in punctuation ("c++", "3.5") can
    never match — the character before the boundary is already a non-word
    character. Anchoring only the ends that are alphanumeric keeps the
    "art"/"start" protection while letting punctuated keywords work at all.
    """
    kw = keyword.strip()
    if not kw:
        return False
    left = r"\b" if kw[0].isalnum() or kw[0] == "_" else ""
    right = r"\b" if kw[-1].isalnum() or kw[-1] == "_" else ""
    return bool(re.search(rf"{left}{re.escape(kw)}{right}", message, re.IGNORECASE))


class IntentStore:
    """CRUD plus deterministic matching for ``standing_intents``."""

    def __init__(self, db: Database, clock: Clock) -> None:
        self._db = db
        self._clock = clock

    async def create(
        self,
        description: str,
        keywords: tuple[str, ...] = (),
        *,
        intent_id: str | None = None,
        channel_scope: str | None = None,
        sender_scope: str | None = None,
        status: IntentStatus = IntentStatus.ARMED,
        fire_budget: int = 3,
        expires_at: datetime | None = None,
    ) -> StandingIntent:
        """Record an intent. Defaults to ``ARMED`` — created means live.

        ``PENDING`` exists for intents that should not match yet (a follow-up
        scheduled for after some other work lands); callers that want that pass
        it explicitly rather than getting it by accident.
        """
        now = self._clock.now()
        iid = intent_id or uuid.uuid4().hex
        await self._db.conn.execute(
            "INSERT INTO standing_intents"
            "(id, description, keywords, channel_scope, sender_scope, status, "
            " fire_budget, fire_count, cooldown_until, expires_at, created_ts, updated_ts) "
            "VALUES (?,?,?,?,?,?,?,0,NULL,?,?,?)",
            (
                iid,
                description,
                json.dumps(list(keywords)),
                channel_scope,
                sender_scope,
                status.value,
                fire_budget,
                expires_at.isoformat() if expires_at else None,
                now.isoformat(),
                now.isoformat(),
            ),
        )
        await self._db.conn.commit()
        created = await self.get(iid)
        if created is None:  # pragma: no cover — INSERT then missing is impossible
            raise RuntimeError(f"standing_intents row {iid!r} vanished after insert")
        _log.info("intent.created", event_type="memory", intent_id=iid, keywords=len(keywords))
        return created

    async def get(self, intent_id: str) -> StandingIntent | None:
        cur = await self._db.conn.execute(f"{_SELECT} WHERE id = ?", (intent_id,))
        row = await cur.fetchone()
        return None if row is None else self._row(row)

    async def active(self) -> list[StandingIntent]:
        """Intents eligible to match right now: live status, unexpired, in budget.

        Cooldown is *not* filtered here — a cooling-down intent is still active,
        it just cannot fire this instant. ``match`` applies that.
        """
        now = self._clock.now().isoformat()
        cur = await self._db.conn.execute(
            f"{_SELECT} WHERE status IN (?,?) "
            "  AND (expires_at IS NULL OR expires_at > ?) "
            "  AND fire_count < fire_budget "
            "ORDER BY created_ts ASC",
            (*_ACTIVE, now),
        )
        return [self._row(r) for r in await cur.fetchall()]

    async def match(
        self,
        message: str,
        *,
        channel: str | None = None,
        sender: str | None = None,
    ) -> list[StandingIntent]:
        """Every active intent whose scope and keywords match this message.

        A keyword-less intent never matches: an intent with no trigger would
        attach itself to every turn, which is exactly the behaviour the budget
        exists to prevent — so it is barred at the match, not just rationed.
        """
        now = self._clock.now()
        out: list[StandingIntent] = []
        for intent in await self.active():
            if intent.cooldown_until is not None and self._still_cooling(intent.cooldown_until, now):
                continue
            if intent.channel_scope is not None and intent.channel_scope != channel:
                continue
            if intent.sender_scope is not None and intent.sender_scope != sender:
                continue
            if not intent.keywords:
                continue
            if any(keyword_matches(k, message) for k in intent.keywords):
                out.append(intent)
        if out:
            _log.info("intent.matched", event_type="memory", count=len(out))
        return out

    async def mark_fired(self, intent_id: str, *, cooldown_s: int = _DEFAULT_COOLDOWN_S) -> bool:
        """Charge one fire against the budget and start the cooldown.

        Transitions to ``DONE`` when the budget is spent, so an exhausted intent
        stops being scanned rather than lingering as a permanently-filtered row.
        """
        intent = await self.get(intent_id)
        if intent is None:
            return False
        now = self._clock.now()
        fire_count = intent.fire_count + 1
        status = IntentStatus.DONE if fire_count >= intent.fire_budget else IntentStatus.FIRED
        # FIRED is not in _ACTIVE, so a still-in-budget intent must go back to
        # ARMED to keep matching; FIRED records that it has surfaced at least
        # once and is only a terminal-ish label for the exhausted case.
        stored = IntentStatus.DONE if status is IntentStatus.DONE else IntentStatus.ARMED
        await self._db.conn.execute(
            "UPDATE standing_intents SET fire_count = ?, status = ?, cooldown_until = ?, updated_ts = ? WHERE id = ?",
            (
                fire_count,
                stored.value,
                (now + timedelta(seconds=max(0, cooldown_s))).isoformat(),
                now.isoformat(),
                intent_id,
            ),
        )
        await self._db.conn.commit()
        _log.info(
            "intent.fired",
            event_type="memory",
            intent_id=intent_id,
            fire_count=fire_count,
            status=stored.value,
        )
        return True

    async def set_status(self, intent_id: str, status: IntentStatus) -> bool:
        """Explicit lifecycle move — arm a pending intent, cancel, or complete."""
        now = self._clock.now().isoformat()
        cur = await self._db.conn.execute(
            "UPDATE standing_intents SET status = ?, updated_ts = ? WHERE id = ?",
            (status.value, now, intent_id),
        )
        await self._db.conn.commit()
        return cur.rowcount == 1

    async def expire_due(self) -> int:
        """Settle stored status for intents past their deadline. Returns the count."""
        now = self._clock.now().isoformat()
        cur = await self._db.conn.execute(
            "UPDATE standing_intents SET status = ?, updated_ts = ? "
            "WHERE status IN (?,?) AND expires_at IS NOT NULL AND expires_at <= ?",
            (IntentStatus.EXPIRED.value, now, *_ACTIVE, now),
        )
        await self._db.conn.commit()
        count = cur.rowcount if cur.rowcount > 0 else 0
        if count:
            _log.info("intent.expired", event_type="memory", count=count)
        return count

    @staticmethod
    def _still_cooling(cooldown_until: datetime, now: datetime) -> bool:
        from datetime import UTC

        left = cooldown_until if cooldown_until.tzinfo else cooldown_until.replace(tzinfo=UTC)
        right = now if now.tzinfo else now.replace(tzinfo=UTC)
        return left > right

    @staticmethod
    def _row(row: object) -> StandingIntent:
        d = dict(row)  # type: ignore[call-overload]
        raw_keywords = d["keywords"] or "[]"
        try:
            parsed = json.loads(raw_keywords)
        except json.JSONDecodeError:  # pragma: no cover — defensive; column is written as JSON
            parsed = []
        keywords = tuple(str(k) for k in parsed) if isinstance(parsed, list) else ()
        return StandingIntent(
            id=d["id"],
            description=d["description"],
            keywords=keywords,
            channel_scope=d["channel_scope"],
            sender_scope=d["sender_scope"],
            status=IntentStatus(d["status"]),
            fire_budget=d["fire_budget"],
            fire_count=d["fire_count"],
            cooldown_until=datetime.fromisoformat(d["cooldown_until"]) if d["cooldown_until"] else None,
            expires_at=datetime.fromisoformat(d["expires_at"]) if d["expires_at"] else None,
            created_ts=datetime.fromisoformat(d["created_ts"]),
            updated_ts=datetime.fromisoformat(d["updated_ts"]),
        )
