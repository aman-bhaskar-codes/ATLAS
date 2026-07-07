import pytest

from atlas.infra.clock import SystemClock
from atlas.infra.db import Database
from atlas.memory.episodic import EpisodicMemory
from atlas.memory.types import Episode, EpisodeKind


@pytest.fixture
async def mem_db(tmp_path):
    db = Database(tmp_path / "test.db")
    await db.start()
    yield db
    await db.stop()

@pytest.mark.asyncio
async def test_episodic_record_and_recent(mem_db):
    clock = SystemClock()
    epi = EpisodicMemory(mem_db, clock)
    ep = Episode(
        correlation_id="c1", ts=clock.now(), kind=EpisodeKind.ACTION,
        role="agent", content="did a thing", tokens=10
    )
    eid = await epi.record(ep)
    assert eid > 0
    
    recent = await epi.recent(10)
    assert len(recent) == 1
    assert recent[0].content == "did a thing"
    assert recent[0].tokens == 10

@pytest.mark.asyncio
async def test_episodic_unconsolidated(mem_db):
    clock = SystemClock()
    epi = EpisodicMemory(mem_db, clock)
    ep = Episode(
        correlation_id="c2", ts=clock.now(), kind=EpisodeKind.MESSAGE,
        role="user", content="hello", tokens=5
    )
    eid = await epi.record(ep)
    
    uncon = await epi.unconsolidated(10)
    assert len(uncon) == 1
    
    await epi.mark_consolidated([eid])
    
    uncon2 = await epi.unconsolidated(10)
    assert len(uncon2) == 0

@pytest.mark.asyncio
async def test_episodic_correction_salience(mem_db):
    clock = SystemClock()
    epi = EpisodicMemory(mem_db, clock)
    await epi.record_correction("c3", "no, do it this way")
    
    recent = await epi.recent(10)
    assert len(recent) == 1
    assert recent[0].salience == 1.0
    assert recent[0].kind == EpisodeKind.CORRECTION
