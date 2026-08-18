"""Tests for idempotency store and API-level idempotency handling."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from atlas.infra.db import Database
from atlas.interfaces.api.idempotency import IdempotencyStore


@pytest.fixture
async def temp_db(tmp_path: Path) -> Database:
    """Create a temporary database with migrations applied."""
    db = Database(tmp_path / "test.db")
    await db.start()
    yield db
    await db.stop()


@pytest.fixture
async def store(temp_db: Database) -> IdempotencyStore:
    """Create an idempotency store backed by the temp database."""
    return IdempotencyStore(temp_db)


class TestIdempotencyStore:
    """Unit tests for idempotency store operations."""

    async def test_fingerprint_generation(self) -> None:
        """Fingerprint should be deterministic and order-independent."""
        payload1 = {"task": "test", "data": {"a": 1, "b": 2}}
        payload2 = {"data": {"b": 2, "a": 1}, "task": "test"}  # Different order

        fp1 = IdempotencyStore.fingerprint(payload1)
        fp2 = IdempotencyStore.fingerprint(payload2)

        assert fp1 == fp2
        assert isinstance(fp1, str)
        assert len(fp1) == 64  # SHA256 hex digest length

    async def test_put_and_get_round_trip(self, store: IdempotencyStore) -> None:
        """Should store and retrieve idempotency keys correctly."""
        key = "test-key-123"
        fingerprint = "abc123def456"
        response_json = json.dumps({"status": "success", "result": 42})

        await store.put(key, fingerprint, response_json)
        result = await store.get(key)

        assert result is not None
        retrieved_fp, retrieved_response = result
        assert retrieved_fp == fingerprint
        assert retrieved_response == response_json

    async def test_get_nonexistent_key(self, store: IdempotencyStore) -> None:
        """Should return None for keys that don't exist."""
        result = await store.get("nonexistent-key")
        assert result is None

    async def test_put_duplicate_key_fails(self, store: IdempotencyStore) -> None:
        """Should raise on duplicate key insert (PRIMARY KEY constraint)."""
        key = "duplicate-key"
        fingerprint = "fp1"
        response = "{}"

        await store.put(key, fingerprint, response)

        # Second insert with same key should fail
        with pytest.raises(Exception):  # noqa: B017 - aiosqlite.IntegrityError in practice
            await store.put(key, "fp2", "{}")

    async def test_multiple_keys_isolated(self, store: IdempotencyStore) -> None:
        """Multiple idempotency keys should be stored independently."""
        await store.put("key1", "fp1", '{"result": 1}')
        await store.put("key2", "fp2", '{"result": 2}')
        await store.put("key3", "fp3", '{"result": 3}')

        result1 = await store.get("key1")
        result2 = await store.get("key2")
        result3 = await store.get("key3")

        assert result1 is not None
        assert result2 is not None
        assert result3 is not None

        assert result1[1] == '{"result": 1}'
        assert result2[1] == '{"result": 2}'
        assert result3[1] == '{"result": 3}'

    async def test_fingerprint_collision_detection(
        self, store: IdempotencyStore
    ) -> None:
        """Should detect when same key used with different payload fingerprint."""
        key = "same-key"
        fp1 = "fingerprint-one"
        fp2 = "fingerprint-two"
        response = "{}"

        await store.put(key, fp1, response)
        retrieved = await store.get(key)

        # This is the application-level check for conflicts
        # (store itself allows overwrite, but idempotency middleware prevents it)
        assert retrieved is not None
        assert retrieved[0] == fp1
        assert retrieved[0] != fp2  # Different payload would have different fp


class TestIdempotencyIntegration:
    """Integration tests simulating API-level idempotency behavior."""

    async def test_idempotent_post_returns_cached_response(
        self, store: IdempotencyStore
    ) -> None:
        """Second identical POST should return cached response without re-execution."""
        # Simulate first request
        payload = {"action": "create", "data": "test"}
        idempotency_key = "req-12345"
        fingerprint = IdempotencyStore.fingerprint(payload)

        # Check if exists (first request)
        cached = await store.get(idempotency_key)
        assert cached is None  # No cache hit

        # Simulate successful execution and cache
        response_data = {"id": "obj-999", "status": "created"}
        response_json = json.dumps(response_data)
        await store.put(idempotency_key, fingerprint, response_json)

        # Simulate second request with same key
        cached = await store.get(idempotency_key)
        assert cached is not None
        cached_fp, cached_response = cached

        # Verify fingerprint matches (same payload)
        assert cached_fp == fingerprint

        # Verify cached response
        cached_data = json.loads(cached_response)
        assert cached_data == response_data

    async def test_idempotent_post_detects_payload_mismatch(
        self, store: IdempotencyStore
    ) -> None:
        """Same key with different payload should detect conflict."""
        idempotency_key = "req-conflict"

        # First request
        payload1 = {"action": "create", "value": 100}
        fp1 = IdempotencyStore.fingerprint(payload1)
        response1 = json.dumps({"id": "obj-100"})
        await store.put(idempotency_key, fp1, response1)

        # Second request with SAME key but DIFFERENT payload
        payload2 = {"action": "create", "value": 200}
        fp2 = IdempotencyStore.fingerprint(payload2)

        # Application-level conflict detection
        cached = await store.get(idempotency_key)
        assert cached is not None
        cached_fp, _ = cached

        # Fingerprints don't match - this is a conflict!
        assert cached_fp != fp2
        # In the real middleware, this would raise IdempotencyConflict

    async def test_database_migration_creates_table(self, temp_db: Database) -> None:
        """Database migration should create idempotency_keys table."""
        # Query the table directly to confirm it exists
        cur = await temp_db.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='idempotency_keys'"
        )
        row = await cur.fetchone()
        assert row is not None
        assert row["name"] == "idempotency_keys"

        # Verify table schema
        cur = await temp_db.conn.execute("PRAGMA table_info(idempotency_keys)")
        columns = await cur.fetchall()
        column_names = [col["name"] for col in columns]

        assert "key" in column_names
        assert "fingerprint" in column_names
        assert "response_json" in column_names
        assert "created_ts" in column_names

    async def test_created_ts_index_exists(self, temp_db: Database) -> None:
        """Should have index on created_ts for cleanup queries."""
        cur = await temp_db.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' "
            "AND tbl_name='idempotency_keys' AND name LIKE '%created_ts%'"
        )
        row = await cur.fetchone()
        assert row is not None
