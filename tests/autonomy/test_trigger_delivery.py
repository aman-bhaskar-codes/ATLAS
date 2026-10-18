"""Automation retries must preserve event identity and expose delivery failures."""

import json
from unittest.mock import AsyncMock

import pytest

from atlas.autonomy.automations import ActionConfig, Automation, AutomationRegistry, TriggerConfig
from atlas.autonomy.trigger_engine import TriggerEngine


def _registry() -> AsyncMock:
    registry = AsyncMock(spec=AutomationRegistry)
    registry.list_all.return_value = [
        Automation(
            id="auto-test",
            name="Delivery",
            description="test",
            trigger_config=TriggerConfig(event_type="external.changed"),
            action_config=ActionConfig(type="task", request_template="Inspect {{ payload.name }}"),
        )
    ]
    return registry


async def test_retry_of_same_event_creates_one_task_and_distinct_events_create_two() -> None:
    created: dict[str, str] = {}

    async def dispatch(request: str, idempotency_key: str) -> None:
        created.setdefault(idempotency_key, request)

    engine = TriggerEngine(_registry(), dispatch_task=dispatch)
    payload = json.dumps({"name": "document"})
    await engine.handle_event("external.changed", payload, "event-1")
    await engine.handle_event("external.changed", payload, "event-1")
    assert list(created.values()) == ["Inspect document"]
    await engine.handle_event("external.changed", payload, "event-2")
    assert list(created.values()) == ["Inspect document", "Inspect document"]


async def test_task_delivery_failure_is_propagated_for_durable_retry() -> None:
    async def fail(request: str, idempotency_key: str) -> None:
        raise RuntimeError("dispatch temporarily unavailable")

    engine = TriggerEngine(_registry(), dispatch_task=fail)
    with pytest.raises(RuntimeError, match="dispatch temporarily unavailable"):
        await engine.handle_event("external.changed", '{"name":"document"}', "event-1")


async def test_registry_failure_is_not_treated_as_successful_delivery() -> None:
    registry = _registry()
    registry.list_all.side_effect = RuntimeError("database temporarily unavailable")
    engine = TriggerEngine(registry)
    with pytest.raises(RuntimeError, match="database temporarily unavailable"):
        await engine.handle_event("external.changed", '{"name":"document"}', "event-1")


async def test_invalid_event_payload_never_executes_wildcard_automation() -> None:
    created: list[str] = []

    async def dispatch(request: str, idempotency_key: str) -> None:
        created.append(request)

    registry = _registry()
    registry.list_all.return_value[0].trigger_config = TriggerConfig(event_type="*")
    engine = TriggerEngine(registry, dispatch_task=dispatch)
    with pytest.raises(ValueError):
        await engine.handle_event("external.changed", "not-json", "event-1")
    assert created == []
