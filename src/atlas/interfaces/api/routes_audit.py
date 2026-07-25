"""API routes for audit trails."""

from typing import Any

from fastapi import APIRouter, Depends

from atlas.interfaces.api.dependencies import get_control_plane
from atlas.interfaces.api.facade import AtlasControlPlane

router = APIRouter(tags=["audit"])


@router.get("/audit")
async def list_audit_events(
    limit: int = 50,
    control_plane: AtlasControlPlane = Depends(get_control_plane),
) -> list[dict[str, Any]]:
    """List recent audit events."""
    # Phase 1: Not fully specified in the prompt schema section, but requested in paths.
    return []
