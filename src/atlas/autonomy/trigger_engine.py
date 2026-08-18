"""Trigger Engine.

Evaluates events against automations and dispatches actions.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

import httpx

from atlas.autonomy.automations import Automation, AutomationRegistry
from atlas.infra.logging import get_logger

_log = get_logger("atlas.autonomy.trigger")


class TriggerEngine:
    """Evaluates events and fires automations."""

    def __init__(self, registry: AutomationRegistry, base_url: str = "http://127.0.0.1:8730") -> None:
        self.registry = registry
        self.base_url = base_url

    async def handle_event(self, topic: str, payload_json: str) -> None:
        """Evaluates an event against all enabled automations."""
        try:
            automations = await self.registry.list_all(enabled_only=True)
        except Exception as e:
            _log.error("trigger.registry_error", error=str(e))
            return

        if not automations:
            return

        try:
            payload = json.loads(payload_json)
        except Exception:
            payload = {}

        for auto in automations:
            if self._matches(topic, payload, auto):
                await self._execute(auto, payload)

    def _matches(self, topic: str, payload: dict[str, Any], auto: Automation) -> bool:
        config = auto.trigger_config
        if config.event_type != "*" and config.event_type != topic:
            return False

        for key, expected in config.filters.items():
            # simple dot-notation extraction
            parts = key.split(".")
            val: Any = payload
            for p in parts:
                if isinstance(val, dict):
                    val = val.get(p)
                else:
                    val = None
                    break
            if val != expected:
                return False
        return True

    async def _execute(self, auto: Automation, payload: dict[str, Any]) -> None:
        _log.info("trigger.matched", automation_id=auto.id, name=auto.name)
        action = auto.action_config

        if action.type == "task":
            # Very simple templating: replace {{ payload }} with JSON or {{ payload.key }}
            req = action.request_template
            if "{{ payload }}" in req:
                req = req.replace("{{ payload }}", json.dumps(payload))
            
            # Very naive replacement for flat keys for v1
            for k, v in payload.items():
                if isinstance(v, (str, int, float, bool)):
                    token = f"{{{{ payload.{k} }}}}"
                    if token in req:
                        req = req.replace(token, str(v))

            async with httpx.AsyncClient() as client:
                try:
                    idempotency_key = f"auto_{auto.id}_{uuid.uuid4().hex[:8]}"
                    resp = await client.post(
                        f"{self.base_url}/api/v1/tasks",
                        json={"request": req, "source": "api", "idempotency_key": idempotency_key},
                        timeout=5.0,
                    )
                    resp.raise_for_status()
                    _log.info("trigger.task_dispatched", automation_id=auto.id, response=resp.json())
                except Exception as e:
                    _log.error("trigger.execute_task_failed", automation=auto.id, error=str(e))
        else:
            _log.warning("trigger.unknown_action_type", type=action.type)
