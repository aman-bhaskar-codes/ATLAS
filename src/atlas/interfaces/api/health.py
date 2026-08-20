"""Health and readiness endpoints for the ATLAS runtime.

These endpoints implement the runtime contract for health checks:
- /live - Process liveness check
- /ready - Readiness check for task acceptance  
- /health - Detailed component health
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel

from atlas.bootstrap.runtime import SystemState

router = APIRouter(tags=["health"])


# Response models
class LivenessResponse(BaseModel):
    """Response for liveness probe."""
    alive: bool
    timestamp: str
    uptime_seconds: float


class ReadinessResponse(BaseModel):
    """Response for readiness probe."""
    ready: bool
    state: str
    timestamp: str
    degraded_components: list[str]
    unavailable_capabilities: list[str]


class ComponentHealthModel(BaseModel):
    """Health status of a single component."""
    name: str
    status: str
    latency_ms: float
    last_success: str | None
    last_failure: str | None
    detail: str


class HealthResponse(BaseModel):
    """Response for detailed health check."""
    overall: str
    timestamp: str
    components: list[ComponentHealthModel]
    uptime_seconds: float


def get_runtime_supervisor(request: Request):
    """Get the runtime supervisor from app state."""
    atlas = request.app.state.atlas
    if atlas.runtime_supervisor is None:
        # Fallback for backward compatibility
        return None
    return atlas.runtime_supervisor


@router.get("/live")
async def liveness_probe(request: Request) -> LivenessResponse:
    """Process liveness check - is the process running?
    
    This endpoint is used by container orchestration to check if the process
    is alive. It has no dependencies and should return quickly.
    """
    # Get startup time from app state or use current time as fallback
    startup_time = getattr(request.app.state, "startup_time", datetime.now(UTC))
    uptime_seconds = (datetime.now(UTC) - startup_time).total_seconds()
    
    return LivenessResponse(
        alive=True,
        timestamp=datetime.now(UTC).isoformat(),
        uptime_seconds=uptime_seconds,
    )


@router.get("/ready")
async def readiness_probe(
    supervisor: Annotated[object, Depends(get_runtime_supervisor)],
    request: Request,
) -> ReadinessResponse:
    """Readiness check - can the system accept tasks?
    
    This endpoint checks if the system is ready to accept tasks by checking
    the runtime state and component health. It's used by load balancers
    and schedulers.
    """
    if supervisor is None:
        # Fallback for backward compatibility - assume ready if no supervisor
        return ReadinessResponse(
            ready=True,
            state="READY",
            timestamp=datetime.now(UTC).isoformat(),
            degraded_components=[],
            unavailable_capabilities=[],
        )
    
    state = supervisor.state
    is_ready = state in (SystemState.READY, SystemState.DEGRADED)
    
    return ReadinessResponse(
        ready=is_ready,
        state=state.value,
        timestamp=datetime.now(UTC).isoformat(),
        degraded_components=supervisor.get_degraded_components(),
        unavailable_capabilities=supervisor.get_unavailable_capabilities(),
    )


@router.get("/health")
async def health_probe(
    supervisor: Annotated[object, Depends(get_runtime_supervisor)],
    request: Request,
) -> HealthResponse:
    """Detailed health check - component-level health information.
    
    This endpoint provides detailed health information for all major components,
    used for diagnostics and monitoring.
    """
    if supervisor is None:
        # Fallback for backward compatibility
        atlas = request.app.state.atlas
        db_ok = await atlas.db.health()
        
        return HealthResponse(
            overall="healthy" if db_ok else "degraded",
            timestamp=datetime.now(UTC).isoformat(),
            components=[
                ComponentHealthModel(
                    name="database",
                    status="healthy" if db_ok else "degraded",
                    latency_ms=0.0,
                    last_success=datetime.now(UTC).isoformat() if db_ok else None,
                    last_failure=None if db_ok else datetime.now(UTC).isoformat(),
                    detail="Connected" if db_ok else "Disconnected",
                )
            ],
            uptime_seconds=0.0,
        )
    
    health_report = supervisor.get_health_report()
    
    # Convert component health to response models
    components = [
        ComponentHealthModel(
            name=health.name,
            status=health.status.value,
            latency_ms=health.latency_ms,
            last_success=health.last_success.isoformat() if health.last_success else None,
            last_failure=health.last_failure.isoformat() if health.last_failure else None,
            detail=health.detail,
        )
        for health in health_report.components.values()
    ]
    
    return HealthResponse(
        overall=health_report.overall_status.value,
        timestamp=health_report.timestamp.isoformat(),
        components=components,
        uptime_seconds=health_report.uptime_seconds,
    )