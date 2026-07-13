"""Identity Platform — the single credential API (ADR-016).

WHY one door: every provider fetches credentials HERE and nowhere else, so there
is exactly one place that touches plaintext, one place that refreshes, one place
that audits access. get_usable_secret() transparently refreshes an expired OAuth2
token and persists the rotation, so providers are blissfully unaware of token
lifecycle. Credential VALUES never enter the audit payload — only the fact of
access, redacted.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from atlas.capabilities.identity.auth.base import AuthStrategy
from atlas.capabilities.identity.errors import CredentialNotFound
from atlas.capabilities.identity.models import Credential, CredentialKind
from atlas.capabilities.identity.secret_store import SecretStore
from atlas.infra.db import Database
from atlas.infra.logging import get_logger

_log = get_logger("atlas.identity")

AuditHook = Callable[..., Awaitable[None]]


class IdentityPlatform:
    def __init__(
        self, *, store: SecretStore, db: Database,
        strategies: dict[CredentialKind, AuthStrategy], audit: AuditHook,
    ) -> None:
        self._store = store
        self._db = db
        self._strategies = strategies
        self._audit = audit

    async def put_credential(self, credential: Credential) -> None:
        await self._store.put(credential.id, credential.secret)
        assert self._db.conn is not None
        await self._db.conn.execute(
            "INSERT OR REPLACE INTO identities(id, kind, provider_hint, expires_at, "
            "scopes, rotated_ts) VALUES (?,?,?,?,?,?)",
            (credential.id, credential.kind.value, credential.provider_hint,
             credential.expires_at.isoformat() if credential.expires_at else None,
             ",".join(credential.scopes),
             credential.rotated_ts.isoformat() if credential.rotated_ts else None))
        await self._db.conn.commit()

    async def _load(self, credential_id: str) -> Credential:
        assert self._db.conn is not None
        cur = await self._db.conn.execute(
            "SELECT * FROM identities WHERE id=?", (credential_id,))
        row = await cur.fetchone()
        secret = await self._store.get(credential_id)
        if row is None or secret is None:
            raise CredentialNotFound(f"no credential {credential_id!r}")
        return Credential(
            id=row["id"], kind=CredentialKind(row["kind"]),
            provider_hint=row["provider_hint"], secret=secret,
            scopes=tuple(row["scopes"].split(",")) if row["scopes"] else ())

    async def get_usable_secret(self, credential_id: str) -> str:
        """Return a ready-to-use secret (fresh access token / API key), refreshing
        + persisting rotation transparently. The provider calls ONLY this."""
        credential = await self._load(credential_id)
        strategy = self._strategies[credential.kind]
        if not await strategy.valid(credential):
            credential = await strategy.refresh(credential)
            await self.put_credential(credential)   # persist rotation
            _log.info("identity.refreshed", event_type="identity", credential=credential_id)
        await self._audit(
            correlation_id="identity", actor="identity", action="credential.access",
            tool=credential.provider_hint, outcome="ok",
            payload={"credential_id": credential_id, "kind": credential.kind.value})
            # NOTE: never the secret itself — redacted by construction
        return await strategy.usable_secret(credential)
