from __future__ import annotations

from typing import Any

import pytest

from atlas.capabilities.identity.auth.api_key import ApiKeyStrategy
from atlas.capabilities.identity.models import Credential, CredentialKind
from atlas.capabilities.identity.platform import IdentityPlatform
from atlas.capabilities.identity.secret_store import SecretStore
from atlas.infra.db import Database


@pytest.mark.asyncio
async def test_get_usable_secret_audits_without_leaking(memory_db: Database) -> None:
    audit_payloads: list[dict[str, Any]] = []
    
    async def mock_audit(**kwargs: Any) -> None:
        audit_payloads.append(kwargs)

    store = SecretStore(memory_db, master_key="test-key")
    platform = IdentityPlatform(
        store=store, db=memory_db,
        strategies={CredentialKind.API_KEY: ApiKeyStrategy()},
        audit=mock_audit
    )
    
    cred = Credential(
        id="c1",
        kind=CredentialKind.API_KEY,
        provider_hint="brave",
        secret="sensitive_api_key_value",
    )
    
    await platform.put_credential(cred)
    
    usable = await platform.get_usable_secret("c1")
    assert usable == "sensitive_api_key_value"
    
    assert len(audit_payloads) == 1
    event = audit_payloads[0]
    assert event["action"] == "credential.access"
    assert event["payload"] == {"credential_id": "c1", "kind": "api_key"}
    
    # Ensure the secret is never in the audit payload
    assert "sensitive_api_key_value" not in str(event)
