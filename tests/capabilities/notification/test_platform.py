"""Tests for the Notification Platform."""

from datetime import UTC, datetime
from typing import Any

import pytest

from atlas.capabilities.notification.builder import build_notification_platform
from atlas.capabilities.notification.domain.models import (
    Notification,
    NotificationKind,
    NotificationPriority,
)
from atlas.infra.clock import Clock
from atlas.infra.db import Database
from atlas.infra.ids import UuidGenerator


class FakeClock(Clock):
    def __init__(self, t: datetime):
        self.t = t
    def now(self) -> datetime:
        return self.t

class FakeGateway:
    async def complete(self, req: Any) -> Any:
        class Resp:
            text = "digest summary"
        return Resp()

class FakeIdentity:
    async def resolve_secret(self, name: str, kind: Any) -> str:
        return "fake-secret"

@pytest.fixture
async def db(tmp_path) -> Database:  # type: ignore
    db_path = tmp_path / "test.db"
    database = Database(db_path)
    await database.start()
    return database

@pytest.fixture
def clock() -> FakeClock:
    return FakeClock(datetime(2026, 7, 13, 12, 0, 0, tzinfo=UTC))

@pytest.mark.asyncio
async def test_notification_routing(db, clock, tmp_path, monkeypatch) -> None:  # type: ignore
    ids = UuidGenerator()
    gw = FakeGateway()
    idx = FakeIdentity()
    
    # Mock DesktopProvider so it doesn't run AppleScript
    from atlas.capabilities.notification.providers.desktop import DesktopProvider
    async def fake_send(*args, **kwargs) -> bool:  # type: ignore
        return True
    monkeypatch.setattr(DesktopProvider, "send", fake_send)
    
    # We will build it from empty config so we only use defaults, plus mock
    with open(tmp_path / "notifications.yaml", "w") as f:
        f.write("quiet_hours: []\nchannels: [{name: 'test:default', provider: 'desktop', address: 'local', priority_floor: 0}]\n")
        
    platform = build_notification_platform(
        config_dir=tmp_path, db=db, clock=clock, ids=ids, gateway=gw,  # type: ignore
        identity=idx, callback_base="http://localhost"  # type: ignore
    )
    
    # Send a Tier-0 task complete notification
    n = Notification(
        id=ids.execution_id(),
        correlation_id="test",  # type: ignore
        kind=NotificationKind.TASK_COMPLETE,
        priority=NotificationPriority.LOW,
        title="Task Done",
        body="...",
        created_ts=clock.now(),
        deliver_in_digest=True
    )
    
    # Low priority should go to digest (no immediate dispatch) if we set deliver_in_digest
    receipt = await platform.notify(n)
    assert receipt is None
    
    # Send a Critical safety alert
    n_crit = Notification(
        id=ids.execution_id(),
        correlation_id="test2",  # type: ignore
        kind=NotificationKind.SAFETY_ALERT,
        priority=NotificationPriority.CRITICAL,
        title="Danger",
        body="...",
        created_ts=clock.now()
    )
    
    receipt2 = await platform.notify(n_crit)
    assert receipt2 is not None
    assert receipt2.delivered is True
    assert receipt2.notification_id == n_crit.id
    
    # Check the db
    cur = await db.conn.execute("SELECT id FROM notif_history")
    rows = await cur.fetchall()
    assert len(rows) == 1
