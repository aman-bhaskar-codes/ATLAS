from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from atlas.capabilities.browser.domain.page import AuthState


class ContextSpec(BaseModel):
    model_config = {"frozen": True}
    incognito: bool = False
    profile_name: str | None = None
    viewport_width: int = 1280
    viewport_height: int = 800
    mobile: bool = False
    geolocation: tuple[float, float] | None = None

class SessionState(BaseModel):
    model_config = {"frozen": True}
    session_id: str
    provider_name: str
    auth_state: AuthState
    storage_state: Any | None = None

class Profile(BaseModel):
    model_config = {"frozen": True}
    name: str
    persistent: bool = True
    credential_id: str | None = None

class BrowserSession(BaseModel):
    model_config = {"frozen": True}
    id: str
    spec: ContextSpec
    state: SessionState
