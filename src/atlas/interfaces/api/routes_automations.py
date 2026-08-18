"""API routes for Automations."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request

from atlas.autonomy.automations import Automation, AutomationRegistry
from atlas.interfaces.api.dependencies import get_atlas

router = APIRouter(prefix="/api/v1/automations", tags=["Automations"])


def get_registry(request: Request) -> AutomationRegistry:
    return AutomationRegistry(get_atlas(request).db)


@router.get("")
async def list_automations(
    enabled_only: bool = False,
    registry: AutomationRegistry = Depends(get_registry)
) -> list[Automation]:
    """List all automations."""
    return await registry.list_all(enabled_only=enabled_only)


@router.post("")
async def create_automation(
    auto: Automation,
    registry: AutomationRegistry = Depends(get_registry)
) -> Automation:
    """Create a new automation."""
    await registry.create(auto)
    return auto


@router.get("/{auto_id}")
async def get_automation(
    auto_id: str,
    registry: AutomationRegistry = Depends(get_registry)
) -> Automation:
    """Get an automation by ID."""
    try:
        return await registry.get(auto_id)
    except Exception as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.put("/{auto_id}")
async def update_automation(
    auto_id: str,
    auto: Automation,
    registry: AutomationRegistry = Depends(get_registry)
) -> Automation:
    """Update an automation."""
    if auto.id != auto_id:
        raise HTTPException(status_code=400, detail="Path ID does not match body ID")
    try:
        await registry.update(auto)
        return await registry.get(auto_id)
    except Exception as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.delete("/{auto_id}")
async def delete_automation(
    auto_id: str,
    registry: AutomationRegistry = Depends(get_registry)
) -> dict[str, str]:
    """Delete an automation."""
    try:
        await registry.delete(auto_id)
        return {"status": "ok", "id": auto_id}
    except Exception as e:
        raise HTTPException(status_code=404, detail=str(e))
