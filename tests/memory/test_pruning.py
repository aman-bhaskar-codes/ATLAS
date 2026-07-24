from datetime import timedelta

import pytest

from atlas.infra.clock import SystemClock
from atlas.infra.db import Database
from atlas.infra.ids import UuidGenerator
from atlas.memory.pruning import Pruner


class FakeGateway:
    async def complete(self, req) -> None:  # type: ignore
        class Resp:
            text = "summarized!"
        return Resp()  # type: ignore

@pytest.fixture
async def prune_db(tmp_path) -> None:  # type: ignore
    db = Database(tmp_path / "test.db")
    await db.start()
    yield db
    await db.stop()

@pytest.mark.asyncio
async def test_pruning_compacts_old_episodes(prune_db) -> None:  # type: ignore
    clock = SystemClock()
    ids = UuidGenerator()
    pruner = Pruner(db=prune_db, gateway=FakeGateway(), ids=ids, clock=clock) # type: ignore
    # an old episode, consolidated, low salience
    old_ts = clock.now() - timedelta(days=40)
    await prune_db.conn.execute(
        "INSERT INTO episodes(correlation_id, ts, kind, content, salience, "
        "consolidated, tokens) VALUES (?,?,?,?,?,?,?)",
        ("c1", old_ts.isoformat(), "message", "old msg", 0.1, 1, 5),
    )
    await prune_db.conn.commit()
    
    stats = await pruner.run()
    assert stats["archived"] == 1
    
    cur = await prune_db.conn.execute("SELECT COUNT(*) as c FROM episodes")
    assert (await cur.fetchone())["c"] == 0
    
    cur2 = await prune_db.conn.execute("SELECT summary FROM memory_archive")
    assert (await cur2.fetchone())["summary"] == "summarized!"
