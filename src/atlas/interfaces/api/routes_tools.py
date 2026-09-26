"""Universal tooling fabric API — Part 1 registry + Part 2 catalog surfaces.

Endpoints
---------
GET  /api/v1/tools                          Live registry (Part 1)
GET  /api/v1/tools/catalog                  Catalog summary (version, counts, sources)
GET  /api/v1/tools/catalog/search           Lexical search with matched fields
GET  /api/v1/tools/catalog/namespaces       Namespace records with tool counts
GET  /api/v1/tools/catalog/tools/{id}/inspect          Full tool inspection
GET  /api/v1/tools/catalog/tools/{id}/operations/{op}/inspect   Operation inspection
GET  /api/v1/tools/catalog/candidates       Deterministic candidates (Part-3 seam)
POST /api/v1/tools/catalog/refresh          Re-sync sources into the catalog

Read-only except refresh. Everything is served from the real persistent
catalog — no second framework, no mocks.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel

router = APIRouter()


class UniversalToolOut(BaseModel):
    """One registered universal tool, as rendered by the CLI and frontend."""

    id: str
    name: str
    namespace: str
    execution_type: str
    adapter: str
    capability: str | None
    operations: list[str]
    status: str
    status_detail: str
    requires_auth: bool
    default_tier: int
    default_tier_name: str
    trust_level: str
    locality: str
    cost_class: str
    description: str


@router.get("/tools", response_model=list[UniversalToolOut])
async def list_universal_tools(request: Request) -> list[UniversalToolOut]:
    atlas: Any = request.app.state.atlas
    fabric = getattr(atlas, "tooling", None)
    if fabric is None:
        return []
    out: list[UniversalToolOut] = []
    for registration in fabric.registry.list_registrations():
        d = registration.definition
        out.append(
            UniversalToolOut(
                id=d.id,
                name=d.name,
                namespace=d.namespace.value,
                execution_type=d.execution_type.value,
                adapter=d.adapter,
                capability=d.capability,
                operations=list(d.operations),
                status=registration.status.state.value,
                status_detail=registration.status.detail,
                requires_auth=d.policy.requires_auth,
                default_tier=int(d.policy.default_tier),
                default_tier_name=d.policy.default_tier.name,
                trust_level=d.policy.trust_level,
                locality=d.locality.value,
                cost_class=d.policy.cost_class.value,
                description=d.description,
            )
        )
    return out


# ── Part 2: persistent catalog surfaces ────────────────────────────────── #


def _catalog(request: Request) -> Any:
    atlas = request.app.state.atlas
    catalog = getattr(getattr(atlas, "tooling", None), "catalog", None)
    if catalog is None:
        raise HTTPException(status_code=503, detail="tool catalog is not available")
    return catalog


class CatalogOperationOut(BaseModel):
    operation_id: str
    name: str
    description: str
    schema_fingerprint: str
    annotations: dict[str, Any]
    status: str


class CatalogToolOut(BaseModel):
    tool_id: str
    tool_name: str
    namespace_id: str
    source_id: str
    provider: str
    adapter: str
    version: str
    definition_version: int
    description: str
    capability: str | None
    operations: list[str]
    execution_type: str
    status: str
    enabled: bool
    availability: str
    requires_auth: bool
    auth_state: str
    cost_class: str
    estimated_latency_ms: int
    safety_tool: str
    default_tier: int
    side_effects: bool
    idempotent: bool
    trust_level: str
    locality: str
    tags: list[str]
    definition_fp: str
    schema_bytes: int
    estimated_schema_tokens: int
    created_ts: str | None
    updated_ts: str | None
    last_seen_ts: str | None
    last_validated_ts: str | None


class CatalogMatchOut(BaseModel):
    tool_id: str
    score: float
    matched_fields: list[str]


@router.get("/tools/catalog")
async def catalog_summary(request: Request) -> dict[str, Any]:
    summary: dict[str, Any] = dict(_catalog(request).summary())
    return summary


@router.get("/tools/catalog/search")
async def catalog_search(
    request: Request,
    q: str = Query(..., min_length=1),
    limit: int = Query(default=20, ge=1, le=100),
) -> list[CatalogMatchOut]:
    matches = _catalog(request).search(q, limit=limit)
    return [
        CatalogMatchOut(
            tool_id=m.tool_id,
            score=m.score,
            matched_fields=list(m.matched_fields),
        )
        for m in matches
    ]


@router.get("/tools/catalog/namespaces")
async def catalog_namespaces(request: Request) -> list[dict[str, Any]]:
    catalog = _catalog(request)
    return [ns.model_dump(mode="json") for ns in catalog.list_namespaces()]


@router.get("/tools/catalog/candidates")
async def catalog_candidates(
    request: Request,
    capability: str | None = None,
    operation: str | None = None,
    namespace: str | None = None,
    execution_type: str | None = None,
    status: str | None = None,
    limit: int | None = Query(default=None, ge=1, le=500),
) -> list[CatalogToolOut]:
    records = _catalog(request).find_candidates(
        capability=capability,
        operation=operation,
        namespace=namespace,
        execution_type=execution_type,
        status=status,
        limit=limit,
    )
    return [_tool_out(r) for r in records]


@router.get("/tools/catalog/tools/{tool_id}/inspect")
async def catalog_inspect(request: Request, tool_id: str) -> dict[str, Any]:
    catalog = _catalog(request)
    record = catalog.inspect(tool_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"tool {tool_id!r} is not in the catalog")
    payload: dict[str, Any] = dict(record.model_dump(mode="json"))
    payload["operations_detail"] = [op.model_dump(mode="json") for op in catalog.inspect_operations(tool_id)]
    return payload


@router.get("/tools/catalog/tools/{tool_id}/operations/{operation}/inspect")
async def catalog_inspect_operation(request: Request, tool_id: str, operation: str) -> dict[str, Any]:
    op = _catalog(request).inspect_operation(tool_id, operation)
    if op is None:
        raise HTTPException(status_code=404, detail=f"operation {operation!r} of {tool_id!r} is not in the catalog")
    data: dict[str, Any] = dict(op.model_dump(mode="json"))
    return data


@router.post("/tools/catalog/refresh")
async def catalog_refresh(request: Request, source_id: str | None = None) -> dict[str, Any]:
    catalog = _catalog(request)
    if source_id:
        result = await catalog.refresh_source(source_id)
        payload: dict[str, Any] = {"synced": [result.model_dump(mode="json")]}
        return payload
    results = await catalog.refresh_all()
    payload_all: dict[str, Any] = {"synced": [r.model_dump(mode="json") for r in results]}
    return payload_all


def _tool_out(record: Any) -> CatalogToolOut:
    return CatalogToolOut(
        tool_id=record.tool_id,
        tool_name=record.tool_name,
        namespace_id=record.namespace_id,
        source_id=record.source_id,
        provider=record.provider,
        adapter=record.adapter,
        version=record.version,
        definition_version=record.definition_version,
        description=record.description,
        capability=record.capability,
        operations=list(record.operations),
        execution_type=record.execution_type.value,
        status=record.status.value,
        enabled=record.enabled,
        availability=record.availability.value,
        requires_auth=record.requires_auth,
        auth_state=record.auth_state.value,
        cost_class=record.cost_class,
        estimated_latency_ms=record.estimated_latency_ms,
        safety_tool=record.safety_tool,
        default_tier=record.default_tier,
        side_effects=record.side_effects,
        idempotent=record.idempotent,
        trust_level=record.trust_level,
        locality=record.locality.value,
        tags=list(record.tags),
        definition_fp=record.definition_fp,
        schema_bytes=record.schema_bytes,
        estimated_schema_tokens=record.estimated_schema_tokens,
        created_ts=record.created_ts.isoformat() if record.created_ts else None,
        updated_ts=record.updated_ts.isoformat() if record.updated_ts else None,
        last_seen_ts=record.last_seen_ts.isoformat() if record.last_seen_ts else None,
        last_validated_ts=record.last_validated_ts.isoformat() if record.last_validated_ts else None,
    )
