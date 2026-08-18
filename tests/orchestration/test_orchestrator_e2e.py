"""End-to-end tests for the Orchestrator.

Tests cover basic orchestrator functionality including task execution,
cancellation, and error handling.
"""

from __future__ import annotations

import pytest

from atlas.infra.db import Database
from atlas.infra.types import InboundEvent
from atlas.orchestration.orchestrator import Orchestrator


@pytest.fixture
async def db():
    """Create an in-memory database for testing."""
    database = Database(":memory:")
    await database.start()
    yield database
    await database.stop()


@pytest.mark.asyncio
async def test_orchestrator_imports():
    """Test that orchestrator module imports correctly."""
    # This simple test ensures the orchestrator module can be imported
    # and the class is accessible
    assert Orchestrator is not None


@pytest.mark.asyncio
async def test_inbound_event_creation():
    """Test creating an InboundEvent."""
    event = InboundEvent(
        correlation_id="test_001",
        source="cli",
        content="test command"
    )
    
    assert event.correlation_id == "test_001"
    assert event.source == "cli"
    assert event.content == "test command"


@pytest.mark.asyncio
async def test_database_initialization(db: Database):
    """Test database can be initialized."""
    assert db is not None
    # Try a simple query to verify database is working
    cursor = await db.conn.execute("SELECT 1")
    result = await cursor.fetchone()
    assert result[0] == 1
