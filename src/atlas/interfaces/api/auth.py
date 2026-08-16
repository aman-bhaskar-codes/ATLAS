"""API authentication — bearer API keys, local-first.

WHY keys-not-required-by-default: ATLAS's contract is a free local single-user
install (localhost binding). Setting ATLAS_API_KEYS (comma-separated) enables
authentication for remote/multi-user exposure. READONLY keys (prefixed
'ro:') can read but never mutate — safety authorization still lives
exclusively in the Safety Engine; this is transport-level identity only.
"""

from __future__ import annotations

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

_bearer = HTTPBearer(auto_error=False)


class Principal:
    __slots__ = ("key_id", "role")

    def __init__(self, key_id: str, role: str) -> None:
        self.key_id = key_id
        self.role = role


ANONYMOUS_LOCAL = Principal(key_id="local", role="admin")


def parse_api_keys(raw: str | None) -> dict[str, str]:
    """Parse 'key1,ro:key2' into {key: role}. ro: prefix → readonly."""
    if not raw:
        return {}
    out: dict[str, str] = {}
    for part in raw.split(","):
        token = part.strip()
        if not token:
            continue
        if token.startswith("ro:"):
            out[token[3:]] = "readonly"
        else:
            out[token] = "admin"
    return out


async def require_principal(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> Principal:
    keys: dict[str, str] = getattr(request.app.state, "api_keys", {})
    if not keys:
        return ANONYMOUS_LOCAL  # local mode: no keys configured, open on localhost

    if credentials is None:
        raise HTTPException(401, "authentication required (Bearer API key)")
    role = keys.get(credentials.credentials)
    if role is None:
        raise HTTPException(401, "invalid API key")
    return Principal(key_id=credentials.credentials[:8], role=role)


async def require_admin(principal: Principal = Depends(require_principal)) -> Principal:
    if principal.role != "admin":
        raise HTTPException(403, "read-only key cannot perform mutations")
    return principal
