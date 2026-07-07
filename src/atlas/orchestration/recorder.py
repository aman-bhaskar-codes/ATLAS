"""Execution recorder — the runtime's episodic write path.

WHY only episodic: per the Phase 3 contract, the hot path writes RAW episodes
only; semantic memory is written solely by consolidation. The recorder is the
single place the loop persists what happened, so provenance is clean.
"""

from __future__ import annotations

from atlas.infra.clock import Clock
from atlas.memory.episodic import EpisodicMemory
from atlas.memory.types import Episode, EpisodeKind
from atlas.orchestration.types import Action, Observation, Thought


class ExecutionRecorder:
    def __init__(self, episodic: EpisodicMemory, clock: Clock) -> None:
        self._epi = episodic
        self._clock = clock

    async def record_thought(self, correlation_id: str, t: Thought) -> None:
        await self._epi.record(Episode(
            correlation_id=correlation_id, ts=self._clock.now(),
            kind=EpisodeKind.MESSAGE, role="agent", content=f"thought: {t.content}",
            step=t.step,
        ))

    async def record_action(self, correlation_id: str, a: Action) -> None:
        await self._epi.record(Episode(
            correlation_id=correlation_id, ts=self._clock.now(),
            kind=EpisodeKind.ACTION, role="agent",
            content=f"{a.kind} {a.tool or ''}.{a.operation or ''} {a.args}",
            tool=a.tool, step=a.step,
        ))

    async def record_observation(self, correlation_id: str, o: Observation) -> None:
        await self._epi.record(Episode(
            correlation_id=correlation_id, ts=self._clock.now(),
            kind=EpisodeKind.OBSERVATION, role="system",
            content=str(o.content)[:1000] if o.ok else (o.error or "error"),
            outcome="ok" if o.ok else "error", step=o.step,
        ))
