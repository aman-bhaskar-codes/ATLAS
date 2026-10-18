"""Canonical credential-path detection usable at every execution boundary."""

from __future__ import annotations

from pathlib import Path

_CRED_NAMES = frozenset({"id_rsa", "id_ed25519", ".env", "credentials", ".netrc", ".pgpass"})
_CRED_SUFFIXES = frozenset({".pem", ".key", ".p12", ".keychain"})
DEFAULT_CREDENTIAL_DIRS = ("~/.ssh", "~/.aws", "~/.config/gcloud", "~/.kube", "~/.gnupg", "~/.docker")


def is_credential_access(paths: list[str], credential_dirs: list[str]) -> tuple[bool, str]:
    try:
        directories = [Path(directory).expanduser().resolve(strict=False) for directory in credential_dirs]
        for raw in paths:
            if not raw:
                return True, "empty path treated as credential risk"
            path = Path(raw).expanduser().resolve(strict=False)
            name, suffix = path.name.casefold(), path.suffix.casefold()
            if name in _CRED_NAMES or name.startswith(".env.") or suffix in _CRED_SUFFIXES:
                return True, f"credential-like file: {path}"
            for directory in directories:
                canonical, protected = str(path).casefold(), str(directory).casefold().rstrip("/")
                if canonical == protected or canonical.startswith(protected + "/"):
                    return True, f"path inside credential dir {directory}: {path}"
        return False, ""
    except Exception as exc:
        return True, f"path resolution error, failing closed: {type(exc).__name__}"
