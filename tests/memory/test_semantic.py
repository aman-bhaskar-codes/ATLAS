import pytest

from atlas.infra.clock import SystemClock
from atlas.infra.db import Database
from atlas.infra.ids import UuidGenerator
from atlas.memory.semantic import SemanticMemory
from atlas.memory.types import FactKind


class FakeVectors:
    async def upsert(self, ref, text, embedding) -> None: pass  # type: ignore
    async def query(self, embedding, k) -> None: return []  # type: ignore
    async def delete(self, ref) -> None: pass  # type: ignore

class FakeEmbedder:
    async def embed(self, text) -> None: return [0.1, 0.2, 0.3]  # type: ignore

@pytest.fixture
async def sem_db(tmp_path) -> None:  # type: ignore
    db = Database(tmp_path / "test.db")
    await db.start()
    yield db
    await db.stop()

@pytest.mark.asyncio
async def test_semantic_versioning(sem_db) -> None:  # type: ignore
    clock = SystemClock()
    ids = UuidGenerator()
    sem = SemanticMemory(sem_db, FakeVectors(), FakeEmbedder(), ids, clock)  # type: ignore
    
    fid = await sem.add_fact(
        "likes apple", FactKind.PREFERENCE, confidence=1.0, salience=0.5, sources=[1]
    )
    
    cur = await sem_db.conn.execute("SELECT * FROM semantic_facts WHERE id=?", (fid,))
    row = await cur.fetchone()
    assert row["superseded_by"] is None
    
    fid2 = await sem.supersede(fid, "likes banana", confidence=1.0)
    
    cur2 = await sem_db.conn.execute("SELECT superseded_by FROM semantic_facts WHERE id=?", (fid,))
    row2 = await cur2.fetchone()
    assert row2["superseded_by"] == fid2
