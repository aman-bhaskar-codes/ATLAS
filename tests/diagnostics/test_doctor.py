from __future__ import annotations

from types import SimpleNamespace
from typing import cast

from atlas.app import Atlas
from atlas.capabilities.identity.platform import IdentityPlatform
from atlas.capabilities.identity.secret_store import SecretStore
from atlas.diagnostics.doctor import _verify_encrypted_store
from atlas.infra.db import Database


async def _noop_audit(**kwargs: object) -> None:
    return None


def _identity(memory_db: Database) -> tuple[IdentityPlatform, SecretStore]:
    store = SecretStore(memory_db, "doctor-test-key")
    identity = IdentityPlatform(store=store, db=memory_db, strategies={}, audit=_noop_audit)
    return identity, store


async def test_verify_encrypted_store_accepts_decryptable_ciphertext(memory_db: Database) -> None:
    identity, store = _identity(memory_db)
    await store.put("credential-1", "secret-value")
    atlas = cast(Atlas, SimpleNamespace(identity=identity))

    ok, detail = await _verify_encrypted_store(atlas)

    assert ok
    assert detail == "1 encrypted secret rows verified"


async def test_verify_encrypted_store_rejects_corrupt_ciphertext(memory_db: Database) -> None:
    identity, _store = _identity(memory_db)
    await memory_db.conn.execute(
        "INSERT INTO secrets(id, ciphertext) VALUES (?, ?)",
        ("credential-1", "not-a-fernet-token"),
    )
    await memory_db.conn.commit()
    atlas = cast(Atlas, SimpleNamespace(identity=identity))

    ok, detail = await _verify_encrypted_store(atlas)

    assert not ok
    assert detail == "vault verification failed: DecryptionError"
