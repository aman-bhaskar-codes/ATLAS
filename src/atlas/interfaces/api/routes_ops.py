"""Ops API — tool runtime health, models/providers, schedules.

Batch 6: operator-console surfaces for the Batch 3 tool runtime and the
intelligence layer.

Endpoints
---------
GET  /api/v1/ops/tools           Tool registry with metadata + live health
GET  /api/v1/ops/models          Model specs from config/models.yaml
GET  /api/v1/ops/providers       Provider health summary
GET  /api/v1/ops/schedules       Cron schedules
POST /api/v1/ops/schedules/{id}/toggle  Enable/disable a schedule
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel

router = APIRouter()

_MODELS_YAML = Path(__file__).resolve().parents[4] / "config" / "models.yaml"


class ToolOut(BaseModel):
    name: str
    operations: list[str]
    description: str
    estimated_latency_ms: int | None
    estimated_cost_usd: float | None
    idempotent: bool | None
    side_effects: bool | None
    supports_rollback: bool | None
    health: float
    latency_ewma_ms: float


class ModelOut(BaseModel):
    id: str
    provider: str
    context_length: int
    usd_per_1m_input: float
    usd_per_1m_output: float
    latency_estimate_ms: int
    capabilities: list[str]
    supports_streaming: bool
    supports_tool_calling: bool
    quality_score: float
    enabled: bool


class ProviderOut(BaseModel):
    name: str
    is_local: bool
    available: bool


class ScheduleOut(BaseModel):
    id: str
    name: str
    cron: str
    enabled: bool


@router.get("/ops/tools", response_model=list[ToolOut])
async def list_tools(request: Request) -> list[ToolOut]:
    atlas = request.app.state.atlas
    registry = atlas.tool_router._registry
    health_tracker = atlas.tool_router._health
    out: list[ToolOut] = []
    for name, ops in registry.registered().items():
        meta = registry.metadata(name)
        out.append(
            ToolOut(
                name=name,
                operations=ops,
                description=meta.description if meta else "",
                estimated_latency_ms=meta.estimated_latency_ms if meta else None,
                estimated_cost_usd=meta.estimated_cost_usd if meta else None,
                idempotent=meta.idempotent if meta else None,
                side_effects=meta.side_effects if meta else None,
                supports_rollback=meta.supports_rollback if meta else None,
                health=health_tracker.health(name),
                latency_ewma_ms=health_tracker.latency_ms(name),
            )
        )
    return out


@router.get("/ops/models", response_model=list[ModelOut])
async def list_models(include_disabled: bool = Query(False)) -> list[ModelOut]:
    from atlas.intelligence.registry.model_registry import ModelRegistry

    registry = ModelRegistry.from_yaml(_MODELS_YAML)
    return [
        ModelOut(
            id=s.id,
            provider=s.provider,
            context_length=s.context_length,
            usd_per_1m_input=s.usd_per_1m_input,
            usd_per_1m_output=s.usd_per_1m_output,
            latency_estimate_ms=s.latency_estimate_ms,
            capabilities=sorted(c.value for c in s.capabilities),
            supports_streaming=s.supports_streaming,
            supports_tool_calling=s.supports_tool_calling,
            quality_score=s.quality_score,
            enabled=s.enabled,
        )
        for s in registry.all(include_disabled)
    ]


@router.get("/ops/providers", response_model=list[ProviderOut])
async def list_providers(request: Request) -> list[ProviderOut]:
    atlas = request.app.state.atlas
    runtime = atlas.gateway._runtime
    health_flags = await atlas.gateway.health()
    out: list[ProviderOut] = []
    for name in runtime._providers.names():
        provider = runtime._providers.get(name)
        out.append(
            ProviderOut(
                name=name,
                is_local=bool(getattr(provider, "is_local", False)),
                available=bool(health_flags.get(name, False)),
            )
        )
    return out


@router.get("/ops/schedules", response_model=list[ScheduleOut])
async def list_schedules(request: Request) -> list[ScheduleOut]:
    atlas = request.app.state.atlas
    rows = await atlas.db.conn.execute("SELECT * FROM schedules ORDER BY name")
    schedules = await rows.fetchall()
    return [
        ScheduleOut(
            id=str(r["id"]),
            name=str(r["description"]),
            cron=str(r["cron_expression"]),
            enabled=bool(r["enabled"]),
        )
        for r in schedules
    ]


@router.post("/ops/schedules/{schedule_id}/toggle", response_model=ScheduleOut)
async def toggle_schedule(request: Request, schedule_id: str) -> ScheduleOut:
    atlas = request.app.state.atlas
    conn = atlas.db.conn
    cur = await conn.execute("SELECT * FROM schedules WHERE id = ?", (schedule_id,))
    row = await cur.fetchone()
    if row is None:
        raise HTTPException(404, f"schedule {schedule_id!r} not found")
    new_enabled = not bool(row["enabled"])
    await conn.execute("UPDATE schedules SET enabled = ? WHERE id = ?", (int(new_enabled), schedule_id))
    await conn.commit()
    return ScheduleOut(
        id=str(row["id"]),
        name=str(row["description"]),
        cron=str(row["cron_expression"]),
        enabled=new_enabled,
    )
