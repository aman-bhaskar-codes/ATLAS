"""Route persistence (Part 3 §55/§67).

A RouteDecision (+ plan + graph) is stored as explicit JSON so any route can
be reconstructed for debugging, RAL experiments, and REPLAY (§67): a replay
re-runs ranking/compilation over the RECORDED candidates, judgment, and
policy — no fresh external calls.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from atlas.infra.db import Database
from atlas.tooling.routing.models import RouteDecision, RouteGraph, RoutePlan


class RouteStore:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def save(
        self,
        *,
        decision: RouteDecision,
        plan: RoutePlan | None = None,
        graph: RouteGraph | None = None,
    ) -> None:
        payload = {
            "decision": decision.model_dump(mode="json"),
            "plan": plan.model_dump(mode="json") if plan else None,
            "graph": graph.model_dump(mode="json") if graph else None,
        }
        await self._db.conn.execute(
            """
            INSERT INTO route_decisions
                (route_id, task_id, correlation_id, objective, domain, strategy,
                 decision_type, catalog_version, payload_json, created_ts)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(route_id) DO UPDATE SET
                payload_json=excluded.payload_json
            """,
            (
                decision.route_id,
                decision.request_id,
                decision.correlation_id,
                decision.objective,
                decision.domain,
                decision.strategy,
                decision.decision_type,
                decision.catalog_version,
                json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str),
                (decision.created_at or datetime.utcnow()).isoformat(),
            ),
        )
        await self._db.conn.commit()

    async def load(self, route_id: str) -> dict[str, Any] | None:
        cur = await self._db.conn.execute("SELECT payload_json FROM route_decisions WHERE route_id = ?", (route_id,))
        row = await cur.fetchone()
        if row is None:
            return None
        loaded: dict[str, Any] = json.loads(str(row["payload_json"]))
        return loaded

    async def recent(self, limit: int = 20) -> list[dict[str, Any]]:
        cur = await self._db.conn.execute(
            "SELECT route_id, task_id, objective, domain, strategy, decision_type, "
            "catalog_version, created_ts FROM route_decisions ORDER BY created_ts DESC LIMIT ?",
            (limit,),
        )
        out: list[dict[str, Any]] = []
        async for row in cur:
            out.append(dict(row))
        return out


__all__ = ["RouteStore"]
