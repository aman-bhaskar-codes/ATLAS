"""API routes for capabilities and providers."""

from fastapi import APIRouter, Depends

from atlas.interfaces.api.dependencies import get_control_plane
from atlas.interfaces.api.facade import AtlasControlPlane
from atlas.interfaces.api.schemas import CapabilityResponse, RuntimeHealthResponse

router = APIRouter(tags=["capabilities"])


@router.get("/capabilities", response_model=list[CapabilityResponse])
async def list_capabilities(
    control_plane: AtlasControlPlane = Depends(get_control_plane),
) -> list[CapabilityResponse]:
    """List all registered capabilities and their current readiness posture."""
    return await control_plane.get_capabilities()


@router.get("/providers/health", response_model=RuntimeHealthResponse)
async def list_providers_health(
    control_plane: AtlasControlPlane = Depends(get_control_plane),
) -> RuntimeHealthResponse:
    """Get detailed health status for capability providers."""
    # Phase 1: reusing runtime_health for now
    return await control_plane.runtime_health()
