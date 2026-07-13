"""Identity error taxonomy. WHY: providers/dispatcher branch on these — a missing
credential (fallback to another provider) differs from an expired one (refresh
and retry) differs from a decryption failure (fatal, do not proceed)."""

from __future__ import annotations

from atlas.capabilities.errors import CapabilityError


class IdentityError(CapabilityError):
    pass


class CredentialNotFound(IdentityError):  # noqa: N818
    provider_switch_helps = True


class CredentialExpired(IdentityError):  # noqa: N818
    retryable = True            # after refresh


class RefreshFailed(IdentityError):  # noqa: N818
    provider_switch_helps = True


class DecryptionError(IdentityError):
    """Master key wrong/missing — fatal, never proceed with a garbled secret."""
