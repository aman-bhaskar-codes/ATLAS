"""Identity domain models.

WHY typed (not dict[str,str]): the platform must know a credential's KIND (to pick
an auth strategy), its EXPIRY (to refresh), and its SCOPES (to validate a provider
is allowed what it asks). A Token carries refresh material; a Credential is the
stored envelope; an Identity groups credentials for one account.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel


class CredentialKind(StrEnum):
    API_KEY = "api_key"
    OAUTH2 = "oauth2"
    JWT = "jwt"
    BROWSER_SESSION = "browser_session"


class Token(BaseModel):
    """OAuth2/JWT token material. refresh_token is the sensitive, long-lived part."""
    model_config = {"frozen": True}
    access_token: str
    refresh_token: str | None = None
    expires_at: datetime | None = None
    scopes: tuple[str, ...] = ()


class Credential(BaseModel):
    """Stored credential envelope. `secret` is the opaque payload (API key, or a
    serialized Token, or a cookie jar) — always encrypted at rest."""
    model_config = {"frozen": True}
    id: str                     # e.g. 'google:anti@gmail.com'
    kind: CredentialKind
    provider_hint: str          # which provider family this serves ('google','github','brave')
    secret: str                 # encrypted blob (opaque to everything but the store)
    expires_at: datetime | None = None
    scopes: tuple[str, ...] = ()
    rotated_ts: datetime | None = None


class Profile(BaseModel):
    model_config = {"frozen": True}
    id: str                     # 'google:anti@gmail.com'
    display_name: str
    email: str | None = None
    accounts: tuple[str, ...] = ()   # credential ids belonging to this profile


class Session(BaseModel):
    """Ephemeral authenticated session (e.g. a live browser profile)."""
    model_config = {"frozen": True}
    id: str
    profile_id: str
    state: str                  # opaque serialized session state (encrypted at rest)
    created_ts: datetime
