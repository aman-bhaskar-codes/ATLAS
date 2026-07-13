"""Encrypted secret store — the vault's disk layer.

WHY key-in-keychain: the SQLite DB holds only ciphertext; the master key lives in
the macOS Keychain (env fallback for CI/dev). Losing the DB file leaks nothing.
WHY Fernet: authenticated symmetric encryption (AES-128-CBC + HMAC) from the
stdlib-adjacent `cryptography` package — battle-tested, no crypto we hand-roll.
Encryption/decryption is the ONLY place plaintext secrets exist in memory, and
they never touch logs or the audit ledger.
"""

from __future__ import annotations

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken

from atlas.capabilities.identity.errors import DecryptionError
from atlas.infra.db import Database


class SecretStore:
    def __init__(self, db: Database, master_key: str) -> None:
        # derive a 32-byte urlsafe key from the master secret
        digest = hashlib.sha256(master_key.encode()).digest()
        self._fernet = Fernet(base64.urlsafe_b64encode(digest))
        self._db = db

    async def put(self, credential_id: str, plaintext: str) -> None:
        token = self._fernet.encrypt(plaintext.encode()).decode()
        assert self._db.conn is not None
        await self._db.conn.execute(
            "INSERT OR REPLACE INTO secrets(id, ciphertext) VALUES (?, ?)",
            (credential_id, token))
        await self._db.conn.commit()

    async def get(self, credential_id: str) -> str | None:
        assert self._db.conn is not None
        cur = await self._db.conn.execute(
            "SELECT ciphertext FROM secrets WHERE id=?", (credential_id,))
        row = await cur.fetchone()
        if row is None:
            return None
        try:
            return self._fernet.decrypt(row["ciphertext"].encode()).decode()
        except InvalidToken as exc:
            raise DecryptionError(f"cannot decrypt {credential_id} (wrong master key?)") from exc

    async def delete(self, credential_id: str) -> None:
        assert self._db.conn is not None
        await self._db.conn.execute("DELETE FROM secrets WHERE id=?", (credential_id,))
        await self._db.conn.commit()
