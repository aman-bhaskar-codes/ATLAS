"""The durable bus must provide the same event identity across delivery retries."""

import asyncio
from typing import Any

from atlas.infra.bus import Event, MessageBus


async def test_global_delivery_keeps_stable_id_across_retries(memory_db: Any) -> None:
    bus = MessageBus(memory_db)
    seen: list[str] = []
    delivered = asyncio.Event()

    async def handler(topic: str, payload: str, event_id: str) -> None:
        assert topic == "external.changed"
        seen.append(event_id)
        if len(seen) == 1:
            raise RuntimeError("temporary failure")
        delivered.set()

    bus.subscribe_global_with_id(handler)
    await bus.publish("external.changed", Event(correlation_id="delivery-test"))
    await bus.start()
    try:
        async with asyncio.timeout(4):
            await delivered.wait()
        assert len(seen) == 2
        assert seen[0] == seen[1]
        assert seen[0]
    finally:
        await bus.close()
