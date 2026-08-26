"""Tests for audit log."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from atlas.infra.types import AuditRecord
from atlas.safety.audit import _GENESIS_HASH, AuditLog, _compute_row_hash


class TestComputeRowHash:
    def test_deterministic(self) -> None:
        h1 = _compute_row_hash("prev", "action", "{}", "2024-01-01T00:00:00")
        h2 = _compute_row_hash("prev", "action", "{}", "2024-01-01T00:00:00")
        assert h1 == h2

    def test_different_inputs_different_hashes(self) -> None:
        h1 = _compute_row_hash("prev1", "action", "{}", "2024-01-01T00:00:00")
        h2 = _compute_row_hash("prev2", "action", "{}", "2024-01-01T00:00:00")
        assert h1 != h2

    def test_hash_length(self) -> None:
        h = _compute_row_hash("prev", "action", "{}", "2024-01-01T00:00:00")
        assert len(h) == 64

    def test_genesis_hash(self) -> None:
        assert _GENESIS_HASH == "0" * 64


class TestAuditLog:
    @pytest.fixture
    def mock_db(self) -> AsyncMock:
        db = AsyncMock()
        db.conn = AsyncMock()
        return db

    @pytest.fixture
    def audit_log(self, mock_db: AsyncMock) -> AuditLog:
        return AuditLog(mock_db)

    def _make_record(self, action: str = "test") -> AuditRecord:
        return AuditRecord(
            correlation_id="test-123",
            ts=datetime.now(UTC),
            actor="test",
            action=action,
            tool=None,
            tier=None,
            decision=None,
            outcome=None,
            payload=None,
            cost_tokens=0,
            cost_usd=0.0,
        )

    @pytest.mark.asyncio
    async def test_record_inserts_event(self, audit_log: AuditLog, mock_db: AsyncMock) -> None:
        mock_cursor = AsyncMock()
        mock_cursor.fetchone = AsyncMock(return_value=None)
        mock_cursor.lastrowid = None
        mock_db.conn.execute = AsyncMock(return_value=mock_cursor)

        record = self._make_record()
        await audit_log.record(record)
        assert mock_db.conn.execute.call_count >= 1

    @pytest.mark.asyncio
    async def test_record_with_payload(self, audit_log: AuditLog, mock_db: AsyncMock) -> None:
        mock_cursor = AsyncMock()
        mock_cursor.fetchone = AsyncMock(return_value=None)
        mock_cursor.lastrowid = 1
        mock_db.conn.execute = AsyncMock(return_value=mock_cursor)

        record = self._make_record()
        record = AuditRecord(
            correlation_id="test-123",
            ts=datetime.now(UTC),
            actor="test",
            action="test_action",
            tool=None,
            tier=None,
            decision=None,
            outcome=None,
            payload={"key": "value"},
            cost_tokens=0,
            cost_usd=0.0,
        )
        await audit_log.record(record)
        assert mock_db.conn.execute.call_count >= 1
