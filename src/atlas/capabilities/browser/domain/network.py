"""Network Intelligence (capture/intercept/HAR) models."""
from __future__ import annotations

from pydantic import BaseModel


class Request(BaseModel):
    model_config = {"frozen": True}
    method: str
    url: str
    resource_type: str = ""
    ts_ms: int = 0

class Response(BaseModel):
    model_config = {"frozen": True}
    url: str
    status: int
    mime: str = ""
    size_bytes: int = 0
    ts_ms: int = 0

class NetworkEvent(BaseModel):
    """Captured per request/response/redirect — API discovery + debugging + research."""
    model_config = {"frozen": True}
    request: Request
    response: Response | None = None
    redirected_from: str | None = None
    failed: bool = False
    error: str = ""
