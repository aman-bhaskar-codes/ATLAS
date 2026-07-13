from __future__ import annotations

import pytest

from atlas.capabilities.identity.errors import DecryptionError
from atlas.capabilities.identity.secret_store import SecretStore
from atlas.infra.db import Database


@pytest.mark.asyncio
async def test_roundtrip_encrypts(memory_db: Database) -> None:
    s = SecretStore(memory_db, master_key="test-key")
    await s.put("c1", "super-secret")
    
    # stored value is ciphertext, not plaintext
    assert memory_db.conn is not None
    cur = await memory_db.conn.execute("SELECT ciphertext FROM secrets WHERE id='c1'")
    row = await cur.fetchone()
    assert row is not None
    assert row["ciphertext"] != "super-secret"
    
    assert await s.get("c1") == "super-secret"


@pytest.mark.asyncio
async def test_wrong_key_fails_closed(memory_db: Database) -> None:
    await SecretStore(memory_db, "key-a").put("c1", "x")
    with pytest.raises(DecryptionError):
        await SecretStore(memory_db, "key-b").get("c1")  # different master key
