import json
from unittest.mock import AsyncMock

import pytest

from atlas.autonomy.automations import ActionConfig, Automation, AutomationRegistry, TriggerConfig
from atlas.autonomy.trigger_engine import TriggerEngine


@pytest.fixture
def registry():
    mock = AsyncMock(spec=AutomationRegistry)
    return mock


@pytest.fixture
def trigger_engine(registry):
    engine = TriggerEngine(registry)
    # mock execute to just record calls
    engine._execute = AsyncMock()
    return engine


@pytest.mark.asyncio
async def test_trigger_engine_matches(trigger_engine, registry):
    auto = Automation(
        name="Test Auto",
        description="A test automation",
        trigger_config=TriggerConfig(event_type="test.event", filters={"status": "failed", "metadata.fatal": True}),
        action_config=ActionConfig(type="task", request_template="Fix {{ payload.error }}"),
    )
    registry.list_all.return_value = [auto]

    # Should match
    payload1 = json.dumps({"status": "failed", "metadata": {"fatal": True}, "error": "OOM"})
    await trigger_engine.handle_event("test.event", payload1)
    assert trigger_engine._execute.call_count == 1

    # Reset
    trigger_engine._execute.reset_mock()

    # Wrong topic
    await trigger_engine.handle_event("other.event", payload1)
    assert trigger_engine._execute.call_count == 0

    # Wrong filter (fatal is False)
    payload2 = json.dumps({"status": "failed", "metadata": {"fatal": False}, "error": "OOM"})
    await trigger_engine.handle_event("test.event", payload2)
    assert trigger_engine._execute.call_count == 0


def test_trigger_matches_logic():
    engine = TriggerEngine(None)
    auto = Automation(
        name="Test Auto",
        description="A test automation",
        trigger_config=TriggerConfig(event_type="*", filters={"issue.labels": "bug"}),
        action_config=ActionConfig(type="task", request_template="Fix"),
    )

    # Matches wildcard topic and dot notation
    assert engine._matches("any.topic", {"issue": {"labels": "bug", "title": "Crash"}}, auto) is True
    assert engine._matches("any.topic", {"issue": {"labels": "feature"}}, auto) is False
