"""API routes for approvals."""

from fastapi import APIRouter, Depends

from atlas.interfaces.api.dependencies import get_control_plane
from atlas.interfaces.api.facade import AtlasControlPlane
from atlas.interfaces.api.schemas import (
    ApprovalDecisionRequest,
    ApprovalResponse,
)

router = APIRouter(tags=["approvals"])


@router.get("/approvals/pending", response_model=list[ApprovalResponse])
async def list_pending_approvals(
    control_plane: AtlasControlPlane = Depends(get_control_plane),
) -> list[ApprovalResponse]:
    """List all pending approval requests."""
    return await control_plane.pending_approvals()


@router.post("/approvals/{approval_id}/decide", response_model=ApprovalResponse)
async def decide_approval(
    approval_id: str,
    command: ApprovalDecisionRequest,
    control_plane: AtlasControlPlane = Depends(get_control_plane),
) -> ApprovalResponse:
    """Approve or deny an approval request."""
    return await control_plane.decide_approval(approval_id, command)
