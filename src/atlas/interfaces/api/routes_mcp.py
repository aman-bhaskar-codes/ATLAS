"""MCP runtime API (Part 5 §92) — server inspection + owner-controlled
lifecycle. Adding/enabling/connecting remain owner actions (§28/§93)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request

router = APIRouter()


def _manager(request: Request) -> Any:
    manager = getattr(request.app.state.atlas, "mcp_manager", None)
    if manager is None:
        raise HTTPException(status_code=503, detail="MCP runtime is not available")
    return manager


@router.get("/mcp/servers")
async def list_servers(request: Request) -> list[dict[str, Any]]:
    return _manager(request).status_all()  # type: ignore[no-any-return]


@router.get("/mcp/servers/{server_id}")
async def get_server(server_id: str, request: Request) -> dict[str, Any]:
    manager = _manager(request)
    try:
        out: dict[str, Any] = dict(manager.status(server_id))
    except Exception:
        raise HTTPException(status_code=404, detail=f"MCP server {server_id!r} not found") from None
    return out


@router.post("/mcp/servers/{server_id}/connect")
async def connect_server(server_id: str, request: Request) -> dict[str, Any]:
    manager = _manager(request)
    try:
        info = await manager.connect(server_id)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"MCP connect failed: {exc}") from exc
    out: dict[str, Any] = dict(info.model_dump(mode="json"))
    return out


@router.post("/mcp/servers/{server_id}/disconnect")
async def disconnect_server(server_id: str, request: Request) -> dict[str, Any]:
    await _manager(request).disconnect(server_id)
    return {"disconnected": server_id}


@router.post("/mcp/servers/{server_id}/refresh")
async def refresh_server(server_id: str, request: Request) -> dict[str, Any]:
    try:
        tools = await _manager(request).refresh(server_id)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"MCP refresh failed: {exc}") from exc
    return {"server_id": server_id, "tools": [d.id for d in tools]}


@router.get("/mcp/servers/{server_id}/tools")
async def server_tools(server_id: str, request: Request) -> list[dict[str, Any]]:
    tools = _manager(request).list_tools(server_id)
    return [
        {
            "id": d.id,
            "name": d.name,
            "description": d.description,
            "operations": list(d.operations),
            "trust_level": d.policy.trust_level,
            "locality": d.locality.value,
        }
        for d in tools
    ]


@router.get("/mcp/servers/{server_id}/resources")
async def server_resources(server_id: str, request: Request) -> list[dict[str, Any]]:
    manager = _manager(request)
    try:
        connection = await manager.ensure_ready(server_id)
        resources = await connection.discover_resources()
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"MCP resource discovery failed: {exc}") from exc
    return [
        {
            "uri": getattr(r, "uri", ""),
            "name": getattr(r, "name", ""),
            "description": getattr(r, "description", ""),
            "mime_type": getattr(r, "mime_type", ""),
        }
        for r in resources
    ]


@router.get("/mcp/servers/{server_id}/prompts")
async def server_prompts(server_id: str, request: Request) -> list[dict[str, Any]]:
    manager = _manager(request)
    try:
        connection = await manager.ensure_ready(server_id)
        prompts = await connection.discover_prompts()
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"MCP prompt discovery failed: {exc}") from exc
    return [
        {
            "name": getattr(p, "name", ""),
            "description": getattr(p, "description", ""),
        }
        for p in prompts
    ]
