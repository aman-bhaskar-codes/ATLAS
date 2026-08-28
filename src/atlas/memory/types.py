"""Memory contracts. WHY frozen models everywhere: memory items cross layers
(into the planner's context) and must not be mutated by consumers."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel


class EpisodeKind(StrEnum):
    MESSAGE = "message"
    ACTION = "action"
    OBSERVATION = "observation"
    CORRECTION = "correction"  # user overrode/undid the agent — highest signal


class OriginClass(StrEnum):
    """Where a memory came from — a security boundary, not a hint.

    WHY this exists: without it, text scraped from a web page and text you
    actually said are indistinguishable once they are both "a memory", so a
    prompt-injected sentence can be recalled forever as if you had authored it.
    Only ``OWNER`` and ``AGENT`` rows are eligible for promotion into the
    curated/semantic tiers; ``UNTRUSTED`` and ``SYSTEM`` are barred structurally
    (a CHECK constraint plus the promotion gate), never by model judgement.

    WHY the write path sets it and never the model: a value inferred from the
    content is a value an attacker can write. See ``docs`` §provenance.
    """

    OWNER = "owner"  # the human said it
    AGENT = "agent"  # ATLAS produced it
    UNTRUSTED = "untrusted"  # web pages, tool output, non-owner senders
    SYSTEM = "system"  # framework/diagnostic chatter


class SessionKind(StrEnum):
    """What kind of session produced the episode.

    Only ``INTERACTIVE`` sessions promote. A cron sweep or a heartbeat poll
    talking to itself must not be able to manufacture durable memory.
    """

    INTERACTIVE = "interactive"
    CRON = "cron"
    HEARTBEAT = "heartbeat"
    SUBAGENT = "subagent"


#: Provenance classes that may ever reach the curated or semantic tiers.
PROMOTABLE_ORIGINS: frozenset[OriginClass] = frozenset({OriginClass.OWNER, OriginClass.AGENT})


class Episode(BaseModel):
    model_config = {"frozen": True}
    id: int | None = None
    correlation_id: str
    task_id: str | None = None
    step: int = 0
    ts: datetime
    kind: EpisodeKind
    role: str | None = None
    content: str
    tool: str | None = None
    outcome: str | None = None
    salience: float = 0.0
    tokens: int = 0
    # ── provenance + write-time recall signals (migration 029) ──────────
    # Defaults are deliberately conservative: AGENT (not OWNER) so an episode
    # recorded by code that has not been taught about provenance yet can be
    # recalled but never impersonates the user.
    origin_class: OriginClass = OriginClass.AGENT
    session_kind: SessionKind = SessionKind.INTERACTIVE
    importance: int | None = None  # 1-10; None = neutral, not a trigger candidate
    trigger_hint: str | None = None  # comma-separated phrases, matched in Lane 1

    @property
    def promotable(self) -> bool:
        """Whether this episode may ever reach the curated/semantic tiers.

        Mirrors the SQL promotion gate exactly so callers can pre-filter in
        Python without the two rules drifting apart.
        """
        return self.origin_class in PROMOTABLE_ORIGINS and self.session_kind is SessionKind.INTERACTIVE


class FactKind(StrEnum):
    PREFERENCE = "preference"
    FACT = "fact"
    SKILL = "skill"
    CONTACT = "contact"
    PROJECT = "project"


class CuratedDoc(BaseModel):
    """One curated surface (MEMORY / USER) plus its compare-and-swap token."""

    model_config = {"frozen": True}
    file_key: str
    content: str
    content_hash: str
    pre_image: str | None = None
    pre_image_hash: str | None = None
    version: int = 1
    updated_ts: datetime


class IntentStatus(StrEnum):
    PENDING = "pending"  # created, not yet eligible to fire
    ARMED = "armed"  # eligible; matched against every inbound message
    FIRED = "fired"  # surfaced at least once, budget remains
    DONE = "done"
    CANCELLED = "cancelled"
    EXPIRED = "expired"


class StandingIntent(BaseModel):
    """Prospective memory — "remember to do X when Y comes up".

    Deliberately a row and not a sentence in a memory document: matching has to
    run against every inbound message, so it must be deterministic and cheap.
    ``fire_budget``/``cooldown_until`` are what stop one intent from attaching
    itself to every turn forever.
    """

    model_config = {"frozen": True}
    id: str
    description: str = ""
    keywords: tuple[str, ...] = ()
    channel_scope: str | None = None
    sender_scope: str | None = None
    status: IntentStatus = IntentStatus.PENDING
    fire_budget: int = 3
    fire_count: int = 0
    cooldown_until: datetime | None = None
    expires_at: datetime | None = None
    created_ts: datetime
    updated_ts: datetime

    @property
    def budget_remaining(self) -> int:
        return max(0, self.fire_budget - self.fire_count)


class RecallHit(BaseModel):
    """One Lane-1 result: an episode plus the arithmetic that ranked it.

    The score is carried alongside the episode so a recall decision is
    inspectable after the fact — you can see *why* something was injected
    without re-running the query.
    """

    model_config = {"frozen": True}
    episode: Episode
    score: float
    matched_terms: tuple[str, ...] = ()


class SemanticFact(BaseModel):
    model_config = {"frozen": True}
    id: str
    version: int = 1
    text: str
    kind: FactKind
    confidence: float = 0.5
    salience: float = 0.5
    source_episode_ids: tuple[int, ...] = ()
    superseded_by: str | None = None
    created_ts: datetime
    updated_ts: datetime


class RetrievedContext(BaseModel):
    """What the planner receives. user_model is ALWAYS present; facts/episodes
    are relevance-retrieved and token-bounded."""

    model_config = {"frozen": True}
    user_model: str
    facts: tuple[SemanticFact, ...] = ()
    recent_episodes: tuple[Episode, ...] = ()
    knowledge_chunks: tuple[dict[str, Any], ...] = ()  # Phase 3: Knowledge store results
    token_estimate: int = 0
    #: The curated tier, rendered. Always present when a Lane-1 recall is wired
    #: in — it is the cheapest and highest-signal thing in the context, so it is
    #: never subject to the relevance budget the other fields compete for.
    curated: str = ""

    def render(self) -> str:
        lines = ["## What I know about you", self.user_model, ""]
        if self.curated.strip():
            lines.append(self.curated.strip())
            lines.append("")
        if self.facts:
            lines.append("## Relevant memory")
            for f in self.facts:
                lines.append(f"- [{f.kind.value}] {f.text}")
            lines.append("")
        if self.knowledge_chunks:
            lines.append("## Knowledge base")
            for chunk in self.knowledge_chunks:
                title = chunk.get("document_title", "Unknown")
                content = chunk.get("content", "")[:200]
                lines.append(f"- [{title}] {content}")
            lines.append("")
        if self.recent_episodes:
            lines.append("## Recent context")
            for e in self.recent_episodes:
                lines.append(f"- ({e.kind.value}) {e.content[:200]}")
        return "\n".join(lines)
