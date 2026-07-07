"""Memory contracts. WHY frozen models everywhere: memory items cross layers
(into the planner's context) and must not be mutated by consumers."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel


class EpisodeKind(StrEnum):
    MESSAGE = "message"
    ACTION = "action"
    OBSERVATION = "observation"
    CORRECTION = "correction"   # user overrode/undid the agent — highest signal


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


class FactKind(StrEnum):
    PREFERENCE = "preference"
    FACT = "fact"
    SKILL = "skill"
    CONTACT = "contact"
    PROJECT = "project"


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
    token_estimate: int = 0

    def render(self) -> str:
        lines = ["## What I know about you", self.user_model, ""]
        if self.facts:
            lines.append("## Relevant memory")
            for f in self.facts:
                lines.append(f"- [{f.kind.value}] {f.text}")
            lines.append("")
        if self.recent_episodes:
            lines.append("## Recent context")
            for e in self.recent_episodes:
                lines.append(f"- ({e.kind.value}) {e.content[:200]}")
        return "\n".join(lines)
