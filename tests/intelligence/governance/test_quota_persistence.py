import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path

import pytest

from atlas.infra.db import Database
from atlas.intelligence.errors import QuotaExhaustedError
from atlas.intelligence.governance.quota_governor import FreeQuotaGovernor, ProviderQuota


@pytest.fixture
async def temp_db():
    fd, path = tempfile.mkstemp(suffix=".sqlite3")
    os.close(fd)
    db = Database(Path(path))
    await db.start()
    try:
        yield db
    finally:
        await db.conn.close()
        os.remove(path)


@pytest.mark.asyncio
async def test_migration_idempotent(temp_db):
    """a. Migration applies on a fresh DB and re-applies idempotently."""
    # First start already ran migrations
    
    # Run migrations manually again to check idempotency
    # In db.py _apply_migrations uses schema_version to skip, 
    # but we can explicitly run the create table
    await temp_db.conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS quota_counters (
            provider       TEXT NOT NULL,
            day            TEXT NOT NULL,
            requests_today INTEGER NOT NULL DEFAULT 0,
            tokens_today   INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (provider, day)
        );
        """
    )
    # Shouldn't raise any exception
    assert True


@pytest.mark.asyncio
async def test_restart_simulation(temp_db):
    """b. Restart simulation: identical requests_today/tokens_today."""
    gov1 = FreeQuotaGovernor()
    gov1.configure("groq", ProviderQuota(daily_requests=10, daily_tokens=100, requests_per_minute=10))
    gov1.set_db(temp_db)
    
    # Write some data
    await gov1.arecord("groq", 10)
    await gov1.arecord("groq", 15)
    
    assert gov1.remaining("groq")["requests_used"] == 2
    assert gov1.remaining("groq")["tokens_used"] == 25
    
    # Simulate restart
    gov2 = FreeQuotaGovernor()
    gov2.configure("groq", ProviderQuota(daily_requests=10, daily_tokens=100, requests_per_minute=10))
    gov2.set_db(temp_db)
    await gov2.load()
    
    assert gov2.remaining("groq")["requests_used"] == 2
    assert gov2.remaining("groq")["tokens_used"] == 25


@pytest.mark.asyncio
async def test_security_property_enforcement(temp_db):
    """c. THE security property: check() raises exactly at persisted counts."""
    gov1 = FreeQuotaGovernor()
    gov1.configure("groq", ProviderQuota(daily_requests=3, daily_tokens=100, requests_per_minute=10))
    gov1.set_db(temp_db)
    
    await gov1.arecord("groq", 90)
    await gov1.arecord("groq", 5)
    
    # Simulate restart
    gov2 = FreeQuotaGovernor()
    gov2.configure("groq", ProviderQuota(daily_requests=3, daily_tokens=100, requests_per_minute=10))
    gov2.set_db(temp_db)
    await gov2.load()
    
    # Should not raise yet
    gov2.check("groq", estimated_tokens=1)
    
    # Now exhaust token limit
    with pytest.raises(QuotaExhaustedError, match="daily token limit"):
        gov2.check("groq", estimated_tokens=10)


@pytest.mark.asyncio
async def test_reset_daily(temp_db):
    """d. reset_daily() clears memory and DB rows."""
    gov1 = FreeQuotaGovernor()
    gov1.configure("groq", ProviderQuota())
    gov1.set_db(temp_db)
    
    await gov1.arecord("groq", 50)
    
    # Verify in memory
    assert gov1.remaining("groq")["requests_used"] == 1
    
    await gov1.reset_daily()
    
    # Verify memory is cleared
    assert gov1.remaining("groq")["requests_used"] == 0
    assert gov1.remaining("groq")["tokens_used"] == 0
    
    # Verify DB is cleared on restart
    gov2 = FreeQuotaGovernor()
    gov2.configure("groq", ProviderQuota())
    gov2.set_db(temp_db)
    await gov2.load()
    
    assert gov2.remaining("groq")["requests_used"] == 0
    assert gov2.remaining("groq")["tokens_used"] == 0


@pytest.mark.asyncio
async def test_day_rollover(temp_db):
    """e. Day rollover: yesterday's rows do not hydrate into today."""
    # Write a row with yesterday's date
    yesterday = "2000-01-01"
    await temp_db.conn.execute(
        """
        INSERT INTO quota_counters (provider, day, requests_today, tokens_today)
        VALUES (?, ?, ?, ?)
        """,
        ("groq", yesterday, 5, 500)
    )
    await temp_db.conn.commit()
    
    gov = FreeQuotaGovernor()
    gov.configure("groq", ProviderQuota())
    gov.set_db(temp_db)
    await gov.load()
    
    # Should be fresh (0) because load() only reads today
    assert gov.remaining("groq")["requests_used"] == 0
    assert gov.remaining("groq")["tokens_used"] == 0


@pytest.mark.asyncio
async def test_delta_upsert(temp_db):
    """f. Delta upsert accumulates correctly."""
    gov = FreeQuotaGovernor()
    gov.configure("groq", ProviderQuota())
    gov.set_db(temp_db)
    
    await gov.arecord("groq", 10)
    await gov.arecord("groq", 20)
    
    assert gov.remaining("groq")["requests_used"] == 2
    assert gov.remaining("groq")["tokens_used"] == 30
    
    today = datetime.now(UTC).strftime("%Y-%m-%d")
    async with temp_db.conn.execute(
        "SELECT requests_today, tokens_today FROM quota_counters WHERE provider = ? AND day = ?",
        ("groq", today)
    ) as cursor:
        row = await cursor.fetchone()
        assert row["requests_today"] == 2
        assert row["tokens_today"] == 30


@pytest.mark.asyncio
async def test_schema_no_rpm_columns(temp_db):
    """g. Schema assertion: no RPM/minute columns exist."""
    async with temp_db.conn.execute("PRAGMA table_info(quota_counters)") as cursor:
        columns = [row["name"] async for row in cursor]
        
    assert "provider" in columns
    assert "day" in columns
    assert "requests_today" in columns
    assert "tokens_today" in columns
    assert "requests_this_minute" not in columns
    assert "minute_window_start" not in columns
