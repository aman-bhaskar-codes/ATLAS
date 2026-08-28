"""Two-gate consolidation: what the model is allowed to read, and to overwrite.

Gate 1 is asserted by feeding the consolidator a database that contains untrusted
rows and then reading the *prompt the model actually received*. That is the only
assertion that proves the filter is pre-model rather than post-model — checking
the return value would pass either way.

Gate 2 is asserted through the curated document's final content: a bad merge must
leave the pre-image intact, and the append degradation must add rather than
replace.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from atlas.infra.db import Database
from atlas.infra.types import ModelResponse, ModelTarget
from atlas.memory.consolidation import _MIN_MERGE_RETENTION, Consolidator
from atlas.memory.curated import MEMORY_KEY, CuratedMemory
from atlas.memory.episodic import EpisodicMemory
from atlas.memory.types import Episode, EpisodeKind, OriginClass, SessionKind
from tests.fakes import FakeClock, FakeIdGen

NOW = datetime(2026, 8, 28, 9, 0, 0, tzinfo=UTC)


class FakeGateway:
    """Records the prompt it was given and replays a canned JSON body."""

    def __init__(self, body: str) -> None:
        self.body = body
        self.prompts: list[str] = []

    async def complete(self, req: object) -> ModelResponse:
        self.prompts.append(getattr(req, "prompt", ""))
        return ModelResponse(text=self.body, target=ModelTarget.CLOUD, model="fake")


class FakeSemantic:
    """No stored facts, so nothing is ever deduped away."""

    def __init__(self) -> None:
        self.added: list[str] = []

    async def semantic_search(self, query: str, k: int = 1) -> list[object]:
        return []

    async def add_fact(self, text: str, kind: object, **kw: object) -> None:
        self.added.append(text)


def _json(*, facts: str = "[]", curated: str = "") -> str:
    return f'{{"facts": {facts}, "user_model_updates": [], "curated_memory": {json.dumps(curated)}}}'


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock(NOW)


@pytest.fixture
async def db(tmp_path: Path):  # type: ignore[no-untyped-def]
    database = Database(tmp_path / "consolidation.db")
    await database.start()
    yield database
    await database.stop()


def _build(db: Database, clock: FakeClock, gateway: FakeGateway, curated: CuratedMemory | None) -> Consolidator:
    return Consolidator(
        episodic=EpisodicMemory(db, clock),
        semantic=FakeSemantic(),  # type: ignore[arg-type]
        gateway=gateway,  # type: ignore[arg-type]
        db=db,
        ids=FakeIdGen(),  # type: ignore[arg-type]
        clock=clock,
        curated=curated,
    )


async def _episode(
    db: Database,
    clock: FakeClock,
    *,
    content: str,
    origin: OriginClass = OriginClass.OWNER,
    session: SessionKind = SessionKind.INTERACTIVE,
) -> int:
    return await EpisodicMemory(db, clock).record(
        Episode(
            correlation_id="c1",
            ts=NOW - timedelta(minutes=1),
            kind=EpisodeKind.MESSAGE,
            content=content,
            origin_class=origin,
            session_kind=session,
            salience=0.9,
        )
    )


# ── Gate 1: the model never reads untrusted content ───────────────────────


async def test_untrusted_and_system_episodes_never_reach_the_prompt(db: Database, clock: FakeClock) -> None:
    await _episode(db, clock, content="OWNER SAID THIS", origin=OriginClass.OWNER)
    await _episode(db, clock, content="INJECTED FROM A WEB PAGE", origin=OriginClass.UNTRUSTED)
    await _episode(db, clock, content="FRAMEWORK CHATTER", origin=OriginClass.SYSTEM)

    gw = FakeGateway(_json())
    result = await _build(db, clock, gw, None).run()

    assert len(gw.prompts) == 1
    prompt = gw.prompts[0]
    assert "OWNER SAID THIS" in prompt
    assert "INJECTED FROM A WEB PAGE" not in prompt
    assert "FRAMEWORK CHATTER" not in prompt
    assert result["episodes"] == 1
    assert result["excluded"] == 2


async def test_non_interactive_sessions_are_excluded(db: Database, clock: FakeClock) -> None:
    # A heartbeat or cron turn is ATLAS talking to itself; promoting it to durable
    # memory is how a system slowly learns its own noise.
    await _episode(db, clock, content="cron chatter", session=SessionKind.CRON)
    await _episode(db, clock, content="heartbeat chatter", session=SessionKind.HEARTBEAT)

    gw = FakeGateway(_json())
    result = await _build(db, clock, gw, None).run()

    assert gw.prompts == []  # no candidates means no model call at all
    assert result == {"episodes": 0, "applied": 0, "proposed": 0, "excluded": 2}


async def test_excluded_episodes_are_marked_consumed(db: Database, clock: FakeClock) -> None:
    # Otherwise every sweep forever re-reads the same ineligible rows.
    await _episode(db, clock, content="injected", origin=OriginClass.UNTRUSTED)
    await _build(db, clock, FakeGateway(_json()), None).run()

    assert await EpisodicMemory(db, clock).unconsolidated(limit=50) == []


async def test_a_parse_failure_leaves_episodes_for_the_next_sweep(db: Database, clock: FakeClock) -> None:
    # Contrast with a Gate-1 exclusion: a mangled model response is transient, so
    # the episodes must stay eligible.
    await _episode(db, clock, content="worth keeping")
    result = await _build(db, clock, FakeGateway("not json at all"), None).run()

    assert result["applied"] == 0
    pending = await EpisodicMemory(db, clock).unconsolidated(limit=50)
    assert [e.content for e in pending] == ["worth keeping"]


# ── Gate 2: merge validation ──────────────────────────────────────────────


@pytest.mark.parametrize(
    ("merged", "pre_image", "reason"),
    [
        ("", "- a line", "empty"),
        ("   \n  ", "- a line", "empty"),
        ("x" * 20_001, "", "too_large"),
        # A model that summarises instead of merging: forty lines in, three out.
        ("short", "y" * 100, "lost_content"),
        ("y" * 100, "y" * 100, None),
        # Growth is always allowed — adding is safe, dropping is not.
        ("y" * 500, "y" * 100, None),
        ("y" * 60, "y" * 100, None),  # exactly at the retention floor
    ],
)
def test_merge_rejection_reasons(merged: str, pre_image: str, reason: str | None) -> None:
    assert Consolidator._merge_rejection(merged, pre_image) == reason


def test_retention_floor_is_a_ratio_of_the_pre_image() -> None:
    pre = "z" * 100
    just_under = "z" * (int(100 * _MIN_MERGE_RETENTION) - 1)
    assert Consolidator._merge_rejection(just_under, pre) == "lost_content"


async def test_a_good_merge_replaces_the_curated_document(db: Database, clock: FakeClock) -> None:
    curated = CuratedMemory(db, clock)
    await curated.create_if_absent(MEMORY_KEY, "- old line\n")
    await _episode(db, clock, content="something happened")

    gw = FakeGateway(_json(curated="- old line\n- new line\n"))
    await _build(db, clock, gw, curated).run()

    doc = await curated.get(MEMORY_KEY)
    assert doc is not None
    assert doc.content == "- old line\n- new line\n"
    # The pre-image travels with the write, so one revert undoes a bad sweep.
    assert doc.pre_image == "- old line\n"


async def test_the_prompt_carries_the_current_curated_document(db: Database, clock: FakeClock) -> None:
    # The model is asked to merge, not to invent — so it has to see what is there.
    curated = CuratedMemory(db, clock)
    await curated.create_if_absent(MEMORY_KEY, "- prefers dark mode\n")
    await _episode(db, clock, content="something happened")

    gw = FakeGateway(_json(curated="- prefers dark mode\n"))
    await _build(db, clock, gw, curated).run()

    assert "prefers dark mode" in gw.prompts[0]


async def test_a_lossy_merge_is_refused_and_degrades_to_an_append(db: Database, clock: FakeClock) -> None:
    pre = "- line one\n- line two\n- line three\n- line four\n- line five\n"
    curated = CuratedMemory(db, clock)
    await curated.create_if_absent(MEMORY_KEY, pre)
    await _episode(db, clock, content="something happened")

    gw = FakeGateway(
        _json(
            facts='[{"text": "user ships on Fridays", "kind": "fact", "confidence": 0.95}]',
            curated="- line one\n",  # the model summarised instead of merging
        )
    )
    await _build(db, clock, gw, curated).run()

    doc = await curated.get(MEMORY_KEY)
    assert doc is not None
    # Nothing was lost, and the new fact still landed.
    for line in ("line one", "line two", "line five"):
        assert line in doc.content
    assert "- user ships on Fridays" in doc.content


async def test_a_rejected_merge_with_nothing_to_append_leaves_the_document_alone(
    db: Database, clock: FakeClock
) -> None:
    pre = "- line one\n- line two\n- line three\n"
    curated = CuratedMemory(db, clock)
    await curated.create_if_absent(MEMORY_KEY, pre)
    await _episode(db, clock, content="something happened")

    # Empty merge, and no high-confidence fact to fall back on.
    gw = FakeGateway(_json(curated=""))
    await _build(db, clock, gw, curated).run()

    doc = await curated.get(MEMORY_KEY)
    assert doc is not None
    assert doc.content == pre


async def test_a_concurrent_write_wins_the_compare_and_swap(db: Database, clock: FakeClock) -> None:
    """A live turn that edits the curated tier mid-sweep must not be clobbered."""
    curated = CuratedMemory(db, clock)
    await curated.create_if_absent(MEMORY_KEY, "- original\n")
    await _episode(db, clock, content="something happened")

    class RacingGateway(FakeGateway):
        async def complete(self, req: object) -> ModelResponse:
            # Simulates the live turn landing during the (slow) model call, which
            # is exactly when the captured hash goes stale.
            await curated.append(MEMORY_KEY, "- written by a live turn")
            return await super().complete(req)

    gw = RacingGateway(
        _json(
            facts='[{"text": "fallback fact", "kind": "fact", "confidence": 0.95}]',
            curated="- original\n- from the stale sweep\n",
        )
    )
    await _build(db, clock, gw, curated).run()

    doc = await curated.get(MEMORY_KEY)
    assert doc is not None
    assert "written by a live turn" in doc.content
    assert "from the stale sweep" not in doc.content
    # The sweep still contributed, via the append that cannot remove anything.
    assert "- fallback fact" in doc.content


async def test_consolidation_without_a_curated_tier_still_works(db: Database, clock: FakeClock) -> None:
    # The curated tier is optional wiring; an older construction site must not
    # start crashing because it passes no `curated=`.
    await _episode(db, clock, content="something happened")
    gw = FakeGateway(_json(facts='[{"text": "a fact", "kind": "fact", "confidence": 0.95}]'))

    result = await _build(db, clock, gw, None).run()
    assert result["applied"] == 1
