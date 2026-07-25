"""FastAPI dependency providers for the ATLAS API layer."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from fastapi import Request

from atlas.app import Atlas

if TYPE_CHECKING:
    from atlas.interfaces.api.control_plane import AtlasTrustPlane
    from atlas.interfaces.api.facade import AtlasControlPlane


def get_atlas(request: Request) -> Atlas:
    """Return the shared Atlas instance stored on app.state during lifespan."""
    return cast(Atlas, request.app.state.atlas)


def get_control_plane(request: Request) -> "AtlasControlPlane":
    from atlas.interfaces.api.facade import DefaultAtlasControlPlane
    return DefaultAtlasControlPlane(
        atlas=request.app.state.atlas,
        event_store=request.app.state.event_store,
    )


def get_trust_plane(request: Request) -> "AtlasTrustPlane":
    from atlas.interfaces.api.trust_facade import DefaultAtlasTrustPlane
    return DefaultAtlasTrustPlane(atlas=request.app.state.atlas)
