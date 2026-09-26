"""Routing fabric API (Part 3 §69) — inspectable routing decisions.

Endpoints
---------
GET  /api/v1/routing/domains            Registered domains + availability
GET  /api/v1/routing/strategies         Registered strategies
POST /api/v1/routing/route              Route a request → decision+plan+graph
GET  /api/v1/routing/routes/{id}        Persisted decision payload (§55)
GET  /api/v1/routing/routes/{id}/explain Structured explanation (§68)
POST /api/v1/routing/routes/{id}/replay Replay from recorded snapshot (§67)
GET  /api/v1/routing/routes             Recent routes

Routing NEVER executes — the plan references candidates whose execution flows
through the existing governed funnels.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

router = APIRouter()


class RouteIn(BaseModel):
    request: str = Field(..., min_length=1)
    task_id: str | None = None
    correlation_id: str | None = None
    capability: str | None = None
    operation: str | None = None


@router.get("/routing/domains")
async def list_domains(request: Request) -> list[dict[str, Any]]:
    return _engine(request).list_domains()  # type: ignore[no-any-return]


@router.get("/routing/strategies")
async def list_strategies(request: Request) -> list[dict[str, Any]]:
    return _engine(request).list_strategies()  # type: ignore[no-any-return]


@router.post("/routing/route")
async def route_request(body: RouteIn, request: Request) -> dict[str, Any]:

    engine = _engine(request)
    atlas: Any = request.app.state.atlas
    task = engine.normalize_request(
        objective=body.request,
        task_id=body.task_id or atlas.ids.task_id(),
        correlation_id=str(body.correlation_id or atlas.ids.correlation_id()),
        source="api",
    )
    result = await engine.route(task, capability=body.capability, operation=body.operation)
    payload: dict[str, Any] = {
        "decision": result.decision.model_dump(mode="json"),
        "plan": result.plan.model_dump(mode="json") if result.plan else None,
        "graph": result.graph.model_dump(mode="json") if result.graph else None,
    }
    return payload


@router.get("/routing/routes")
async def recent_routes(request: Request, limit: int = 20) -> list[dict[str, Any]]:
    return await _engine(request).recent_routes(limit=min(max(limit, 1), 100))  # type: ignore[no-any-return]


@router.get("/routing/routes/{route_id}")
async def inspect_route(route_id: str, request: Request) -> dict[str, Any]:
    payload = await _engine(request).inspect_route(route_id)
    if payload is None:
        raise HTTPException(status_code=404, detail=f"route {route_id!r} not found")
    out: dict[str, Any] = dict(payload)
    return out


@router.get("/routing/routes/{route_id}/explain")
async def explain_route(route_id: str, request: Request) -> dict[str, Any]:
    payload = await _engine(request).explain(route_id)
    if payload is None:
        raise HTTPException(status_code=404, detail=f"route {route_id!r} not found")
    out: dict[str, Any] = dict(payload)
    return out


@router.post("/routing/routes/{route_id}/replay")
async def replay_route(route_id: str, request: Request) -> dict[str, Any]:
    result = await _engine(request).replay(route_id)
    if result is None:
        raise HTTPException(status_code=404, detail=f"route {route_id!r} not found (or replay unavailable)")
    payload: dict[str, Any] = {
        "decision": result.decision.model_dump(mode="json"),
        "plan": result.plan.model_dump(mode="json") if result.plan else None,
        "graph": result.graph.model_dump(mode="json") if result.graph else None,
    }
    return payload


def _engine(request: Request) -> Any:
    engine = getattr(request.app.state.atlas, "routing", None)
    if engine is None:
        raise HTTPException(status_code=503, detail="routing fabric is not available")
    return engine
