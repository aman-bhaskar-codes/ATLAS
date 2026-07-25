"""API routes for runtime status and health."""

from fastapi import APIRouter, Depends

from atlas.interfaces.api.dependencies import get_control_plane
from atlas.interfaces.api.facade import AtlasControlPlane
from atlas.interfaces.api.schemas import RuntimeHealthResponse, RuntimeStatusResponse

router = APIRouter(tags=["runtime"])


@router.get("/runtime/status", response_model=RuntimeStatusResponse)
async def get_runtime_status(
    control_plane: AtlasControlPlane = Depends(get_control_plane),
) -> RuntimeStatusResponse:
    """Get the current operational status of the ATLAS runtime."""
    return await control_plane.runtime_status()


@router.get("/runtime/health", response_model=RuntimeHealthResponse)
async def get_runtime_health(
    control_plane: AtlasControlPlane = Depends(get_control_plane),
) -> RuntimeHealthResponse:
    """Get the health checks for ATLAS components and providers."""
    return await control_plane.runtime_health()
