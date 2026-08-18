import pytest

from atlas.autonomy.automations import ActionConfig, Automation, AutomationRegistry, TriggerConfig
from atlas.infra.db import Database


@pytest.fixture
async def memory_db():
    db = Database(":memory:")
    await db.start()
    yield db
    await db.stop()


@pytest.fixture
def registry(memory_db):
    return AutomationRegistry(memory_db)


@pytest.mark.asyncio
async def test_automation_crud(registry):
    auto = Automation(
        name="Test Auto",
        description="A test automation",
        trigger_config=TriggerConfig(event_type="test.event", filters={"payload.status": "failed"}),
        action_config=ActionConfig(type="task", request_template="Fix {{ payload.error }}"),
    )

    await registry.create(auto)

    fetched = await registry.get(auto.id)
    assert fetched.name == "Test Auto"
    assert fetched.trigger_config.event_type == "test.event"
    assert fetched.action_config.request_template == "Fix {{ payload.error }}"
    assert fetched.enabled is True

    fetched.name = "Updated Auto"
    fetched.enabled = False
    await registry.update(fetched)

    updated = await registry.get(auto.id)
    assert updated.name == "Updated Auto"
    assert updated.enabled is False

    all_autos = await registry.list_all()
    assert len(all_autos) == 1
    
    enabled_autos = await registry.list_all(enabled_only=True)
    assert len(enabled_autos) == 0

    await registry.delete(auto.id)
    
    autos = await registry.list_all()
    assert len(autos) == 0
