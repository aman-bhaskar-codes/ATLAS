"""Trigger Engine.

Evaluates events against automations and dispatches actions.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Awaitable, Callable
from typing import Any

import httpx

from atlas.autonomy.automations import Automation, AutomationRegistry
from atlas.infra.logging import get_logger

_log = get_logger("atlas.autonomy.trigger")


class TriggerEngine:
    """Evaluates events and fires automations."""

    def __init__(
        self,
        registry: AutomationRegistry,
        base_url: str = "http://127.0.0.1:8730",
        *,
        dispatch_task: Callable[[str, str], Awaitable[None]] | None = None,
        api_key: str | None = None,
    ) -> None:
        self.registry = registry
        self.base_url = base_url
        self._dispatch_task = dispatch_task
        self._api_key = api_key

    async def handle_event(self, topic: str, payload_json: str, event_id: str | None = None) -> None:
        """Evaluates an event against all enabled automations."""
        try:
            automations = await self.registry.list_all(enabled_only=True)
        except Exception as e:
            _log.error("trigger.registry_error", error_type=type(e).__name__)
            raise

        if not automations:
            return

        payload = json.loads(payload_json)
        if not isinstance(payload, dict):
            raise ValueError("automation event payload must be an object")
        # Legacy two-argument callers lack envelope identity. The actual bus
        # supplies its durable row ID so distinct identical events stay distinct.
        identity = event_id or hashlib.sha256(f"{topic}\0{payload_json}".encode()).hexdigest()

        for auto in automations:
            if self._matches(topic, payload, auto):
                await self._execute(auto, payload, identity)

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

    async def _execute(self, auto: Automation, payload: dict[str, Any], event_id: str) -> None:
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

            identity = hashlib.sha256(f"{auto.id}\0{event_id}".encode()).hexdigest()
            idempotency_key = f"auto_{identity}"
            if self._dispatch_task is not None:
                # Reuses the same durable task/control-plane entrypoint as HTTP;
                # no localhost port assumption and no transport key dependency.
                await self._dispatch_task(req, idempotency_key)
                return
            async with httpx.AsyncClient() as client:
                try:
                    resp = await client.post(
                        f"{self.base_url}/api/v1/tasks",
                        json={"request": req, "source": "api", "idempotency_key": idempotency_key},
                        headers={"Authorization": f"Bearer {self._api_key}"} if self._api_key else {},
                        timeout=5.0,
                    )
                    resp.raise_for_status()
                    _log.info("trigger.task_dispatched", automation_id=auto.id, response=resp.json())
                except Exception as e:
                    _log.error("trigger.execute_task_failed", automation=auto.id, error_type=type(e).__name__)
                    raise
        else:
            raise ValueError(f"unsupported automation action type: {action.type}")
