"""API authentication — bearer API keys, local-first.

WHY keys-not-required-by-default: ATLAS's contract is a free local single-user
install (localhost binding). Setting ATLAS_API_KEYS (comma-separated) enables
authentication for remote/multi-user exposure. READONLY keys (prefixed
'ro:') can read but never mutate — safety authorization still lives
exclusively in the Safety Engine; this is transport-level identity only.
"""

from __future__ import annotations

from fastapi import Depends, HTTPException, Request, WebSocket, WebSocketException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

_bearer = HTTPBearer(auto_error=False)

# Shared with HTTP CORS: browser handshakes must originate at the dashboard.
BROWSER_ORIGINS = (
    "http://localhost:3000",
    "http://127.0.0.1:3000",
    "http://localhost:3001",
    "http://127.0.0.1:3001",
    "http://localhost:3030",
    "http://127.0.0.1:3030",
    "http://localhost:3100",
    "http://127.0.0.1:3100",
)
_WS_KEY_PREFIX = "atlas.api-key."


class Principal:
    __slots__ = ("key_id", "role")

    def __init__(self, key_id: str, role: str) -> None:
        self.key_id = key_id
        self.role = role


ANONYMOUS_LOCAL = Principal(key_id="local", role="admin")


def parse_api_keys(raw: str | None) -> dict[str, str]:
    """Parse 'key1,ro:key2' into {key: role}. ro: prefix → readonly.

    Empty key material is dropped rather than stored. Without that check
    ``ATLAS_API_KEYS="ro:"`` produced ``{"": "readonly"}`` — a non-empty key map,
    so the server switched to enforcing mode, keyed on a token no caller can ever
    send (``bearer_token`` rejects an empty token). Every request would 401 with
    no way to authenticate. Returning ``{}`` instead lets ``create_app`` detect
    "keys were configured but none are usable" and refuse to start.
    """
    if not raw:
        return {}
    out: dict[str, str] = {}
    for part in raw.split(","):
        token = part.strip()
        if not token:
            continue
        if token.startswith("ro:"):
            key, role = token[3:], "readonly"
        else:
            key, role = token, "admin"
        if not key:
            continue
        out[key] = role
    return out


def bearer_token(authorization: str | None) -> str | None:
    """Extract the token from an ``Authorization: Bearer <token>`` header."""
    if not authorization:
        return None
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer":
        return None
    return token.strip() or None


def resolve_principal(
    keys: dict[str, str],
    authorization: str | None,
    x_api_key: str | None = None,
) -> Principal | None:
    """Best-effort identity from a raw header. Never raises.

    WHY a non-raising variant exists alongside ``require_principal``: the quota
    middleware needs the caller's identity BEFORE the route dependency stack
    runs (middleware wraps routing), but middleware is the wrong place to reject
    — a raised exception there bypasses the handler stack. So the middleware
    uses this to key the token bucket per API key, and ``require_principal``
    (a route dependency) is what actually returns 401.

    Returns ``ANONYMOUS_LOCAL`` when no keys are configured, the matching
    ``Principal`` for a known token, and ``None`` when keys ARE configured but
    the caller presented nothing valid — an unidentified caller, whose quota
    therefore falls back to per-IP.
    """
    if not keys:
        return ANONYMOUS_LOCAL  # local mode: no keys configured, open on localhost
    token = bearer_token(authorization) or (x_api_key.strip() if x_api_key else None)
    if token is None:
        return None
    role = keys.get(token)
    if role is None:
        return None
    return Principal(key_id=token[:8], role=role)


async def require_principal(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> Principal:
    """Route dependency: 401 unless the caller is identified.

    In local mode (no ``ATLAS_API_KEYS``) this returns ``ANONYMOUS_LOCAL`` and
    changes nothing — which is what makes wiring it onto every router safe to do
    by default. Setting the env var is what starts enforcing.
    """
    keys: dict[str, str] = getattr(request.app.state, "api_keys", {})
    if not keys:
        request.state.principal = ANONYMOUS_LOCAL
        return ANONYMOUS_LOCAL

    token = credentials.credentials if credentials else request.headers.get("x-atlas-api-key")
    if not token:
        raise HTTPException(401, "authentication required (Bearer API key)")
    role = keys.get(token)
    if role is None:
        raise HTTPException(401, "invalid API key")
    principal = Principal(key_id=token[:8], role=role)
    request.state.principal = principal
    return principal


async def require_admin(principal: Principal = Depends(require_principal)) -> Principal:
    if principal.role != "admin":
        raise HTTPException(403, "read-only key cannot perform mutations")
    return principal


async def require_websocket_principal(websocket: WebSocket) -> Principal:
    """Validate identity and browser Origin before accepting the handshake.

    Native clients use Authorization. Browsers may offer protocols ``atlas``
    and ``atlas.api-key.<key>``; only ``atlas`` is negotiated, so credentials
    are never echoed. Query parameters are deliberately not credentials.
    Missing Origin is valid for native clients. Local mode still checks any
    supplied Origin because a hostile page can otherwise reach localhost.
    """
    origin = websocket.headers.get("origin")
    if origin is not None and origin not in BROWSER_ORIGINS:
        raise WebSocketException(code=1008, reason="origin not allowed")

    keys: dict[str, str] = getattr(websocket.app.state, "api_keys", {})
    authorization = websocket.headers.get("authorization")
    if authorization is None:
        tokens = [
            protocol.removeprefix(_WS_KEY_PREFIX)
            for protocol in websocket.scope.get("subprotocols", [])
            if protocol.startswith(_WS_KEY_PREFIX)
        ]
        if len(tokens) == 1:
            authorization = f"Bearer {tokens[0]}"
    principal = resolve_principal(keys, authorization)
    if principal is None:
        raise WebSocketException(code=1008, reason="authentication required")
    websocket.state.principal = principal
    return principal


async def require_websocket_admin(websocket: WebSocket) -> Principal:
    principal = await require_websocket_principal(websocket)
    if principal.role != "admin":
        raise WebSocketException(code=1008, reason="read-only key cannot perform mutations")
    return principal


def websocket_subprotocol(websocket: WebSocket) -> str | None:
    """Select only the public protocol; never negotiate credential material."""
    return "atlas" if "atlas" in websocket.scope.get("subprotocols", []) else None
