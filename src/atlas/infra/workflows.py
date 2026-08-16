"""Workflow template store — learned automations from successful tasks.

WHY: Vamos specifies that ATLAS should learn from successful multi-step tasks
and distill them into reusable workflow templates. When a similar task comes in
later, the system can propose a known-good template instead of planning from
scratch. This is the "learning" in the evaluation loop.
"""

from __future__ import annotations

import json

from atlas.infra.clock import Clock
from atlas.infra.db import Database
from atlas.infra.ids import IdGenerator
from atlas.infra.logging import get_logger

_log = get_logger("atlas.workflows")


class WorkflowStore:
    def __init__(self, db: Database, ids: IdGenerator, clock: Clock) -> None:
        self._db = db
        self._ids = ids
        self._clock = clock

    async def save_template(
        self,
        *,
        name: str,
        description: str,
        steps: list[dict[str, object]],
        variables: list[str] | None = None,
        derived_from: list[str] | None = None,
    ) -> str:
        """Save a workflow template derived from a successful task execution."""
        wid = self._ids.execution_id()
        await self._db.conn.execute(
            "INSERT INTO workflow_templates(id, name, description, steps, "
            "variables, derived_from, created_ts) VALUES (?,?,?,?,?,?,?)",
            (
                wid,
                name,
                description,
                json.dumps(steps),
                json.dumps(variables or []),
                json.dumps(derived_from or []),
                self._clock.now().isoformat(),
            ),
        )
        await self._db.conn.commit()
        _log.info("workflow.saved", event_type="workflow", id=wid, name=name, steps=len(steps))
        return wid

    async def find_similar(self, description: str, limit: int = 5) -> list[dict[str, object]]:
        """Find workflow templates with similar descriptions (keyword match).

        Future: use embedding similarity search for better matching.
        """
        keywords = description.lower().split()[:5]  # first 5 words as filter
        conditions = " OR ".join(["LOWER(description) LIKE ?" for _ in keywords])
        params = [f"%{kw}%" for kw in keywords]
        cur = await self._db.conn.execute(
            f"SELECT * FROM workflow_templates WHERE {conditions} ORDER BY use_count DESC, success_rate DESC LIMIT ?",
            (*params, limit),
        )
        return [dict(r) for r in await cur.fetchall()]

    async def record_use(self, template_id: str, succeeded: bool) -> None:
        """Record that a template was used and whether it succeeded."""
        await self._db.conn.execute(
            "UPDATE workflow_templates SET use_count = use_count + 1 WHERE id=?",
            (template_id,),
        )
        # Update rolling success rate
        cur = await self._db.conn.execute(
            "SELECT use_count, success_rate FROM workflow_templates WHERE id=?",
            (template_id,),
        )
        row = await cur.fetchone()
        if row:
            old_rate = float(row["success_rate"]) if row["success_rate"] is not None else 0.0
            # Exponential moving average
            new_rate = old_rate * 0.8 + (1.0 if succeeded else 0.0) * 0.2
            await self._db.conn.execute(
                "UPDATE workflow_templates SET success_rate=? WHERE id=?",
                (new_rate, template_id),
            )
        await self._db.conn.commit()

    async def list_templates(self, limit: int = 50) -> list[dict[str, object]]:
        """List all workflow templates, sorted by usage."""
        cur = await self._db.conn.execute("SELECT * FROM workflow_templates ORDER BY use_count DESC LIMIT ?", (limit,))
        return [dict(r) for r in await cur.fetchall()]

    async def get(self, template_id: str) -> dict[str, object] | None:
        """Get a specific workflow template by ID."""
        cur = await self._db.conn.execute("SELECT * FROM workflow_templates WHERE id=?", (template_id,))
        row = await cur.fetchone()
        return dict(row) if row else None
