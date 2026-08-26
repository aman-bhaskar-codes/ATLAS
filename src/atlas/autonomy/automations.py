"""Automation Registry and Models.

Automations map an event (Trigger) to an execution (Action).
They are persisted in the `automations` SQLite table.
"""

from __future__ import annotations

import uuid
from typing import Any

from pydantic import BaseModel, Field

from atlas.infra.db import Database
from atlas.infra.errors import NotFoundError


def now_iso8601() -> str:
    from datetime import UTC, datetime

    return datetime.now(UTC).isoformat()


class TriggerConfig(BaseModel):
    """Defines the conditions under which an automation triggers."""

    event_type: str
    filters: dict[str, Any] = Field(default_factory=dict)


class ActionConfig(BaseModel):
    """Defines the action to take when triggered."""

    type: str  # e.g., "task"
    request_template: str


class Automation(BaseModel):
    """The core Automation model."""

    id: str = Field(default_factory=lambda: f"auto_{uuid.uuid4().hex[:12]}")
    name: str
    description: str
    enabled: bool = True
    trigger_config: TriggerConfig
    action_config: ActionConfig
    created_ts: str = Field(default_factory=now_iso8601)
    updated_ts: str = Field(default_factory=now_iso8601)


class AutomationRegistry:
    """CRUD repository for Automations."""

    def __init__(self, db: Database) -> None:
        self.db = db

    async def create(self, auto: Automation) -> None:
        await self.db.conn.execute(
            """
            INSERT INTO automations (
                id, name, description, enabled, trigger_config, action_config, created_ts, updated_ts
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                auto.id,
                auto.name,
                auto.description,
                1 if auto.enabled else 0,
                auto.trigger_config.model_dump_json(),
                auto.action_config.model_dump_json(),
                auto.created_ts,
                auto.updated_ts,
            ),
        )
        await self.db.conn.commit()

    async def get(self, auto_id: str) -> Automation:
        cur = await self.db.conn.execute("SELECT * FROM automations WHERE id = ?", (auto_id,))
        row = await cur.fetchone()
        if not row:
            raise NotFoundError(f"Automation {auto_id} not found")
        return self._row_to_auto(row)

    async def list_all(self, *, enabled_only: bool = False) -> list[Automation]:
        query = "SELECT * FROM automations"
        if enabled_only:
            query += " WHERE enabled = 1"
        query += " ORDER BY created_ts DESC"

        cur = await self.db.conn.execute(query)
        rows = await cur.fetchall()
        return [self._row_to_auto(row) for row in rows]

    async def update(self, auto: Automation) -> None:
        auto.updated_ts = now_iso8601()
        cur = await self.db.conn.execute(
            """
            UPDATE automations
            SET name = ?, description = ?, enabled = ?, trigger_config = ?, action_config = ?, updated_ts = ?
            WHERE id = ?
            """,
            (
                auto.name,
                auto.description,
                1 if auto.enabled else 0,
                auto.trigger_config.model_dump_json(),
                auto.action_config.model_dump_json(),
                auto.updated_ts,
                auto.id,
            ),
        )
        if cur.rowcount == 0:
            raise NotFoundError(f"Automation {auto.id} not found")
        await self.db.conn.commit()

    async def delete(self, auto_id: str) -> None:
        cur = await self.db.conn.execute("DELETE FROM automations WHERE id = ?", (auto_id,))
        if cur.rowcount == 0:
            raise NotFoundError(f"Automation {auto_id} not found")
        await self.db.conn.commit()

    def _row_to_auto(self, row: Any) -> Automation:
        return Automation(
            id=row["id"],
            name=row["name"],
            description=row["description"],
            enabled=bool(row["enabled"]),
            trigger_config=TriggerConfig.model_validate_json(row["trigger_config"]),
            action_config=ActionConfig.model_validate_json(row["action_config"]),
            created_ts=row["created_ts"],
            updated_ts=row["updated_ts"],
        )
