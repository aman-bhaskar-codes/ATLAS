"""Hard-block matchers.

WHY canonicalize + resolve symlinks BEFORE testing: substring checks on '.ssh'
are trivially defeated by traversal or a symlink. We resolve to a real path and
test containment. WHY fail-closed on unresolvable: an ambiguous path near
credentials is treated as a hit. These functions never raise into the caller —
any internal error returns a hit.
"""

from __future__ import annotations

from pathlib import Path
from urllib.parse import urlparse

_CRED_NAMES = {"id_rsa", "id_ed25519", ".env", "credentials", ".netrc", ".pgpass"}
_CRED_SUFFIXES = {".pem", ".key", ".p12", ".keychain"}
_FIN_PATH_HINTS = ("/charge", "/payment", "/payout", "/transfer", "/withdraw", "/checkout")
_FIN_CLI_HINTS = ("stripe", "paypal", "razorpay", "web3", "eth-", "coinbase")

KNOWN_MATCHERS: frozenset[str] = frozenset(
    {"credential_access", "mass_deletion", "financial_transaction", "edit_safety_config"}
)


def _canonical(raw: str) -> Path | None:
    if not raw:
        return None
    try:
        return Path(raw).expanduser().resolve(strict=False)
    except (OSError, RuntimeError, ValueError):
        return None


def is_credential_access(paths: list[str], credential_dirs: list[str]) -> tuple[bool, str]:
    try:
        cred_dirs = [Path(d).expanduser().resolve(strict=False) for d in credential_dirs]
        for raw in paths:
            p = _canonical(raw)
            if p is None:
                return True, f"unresolvable path treated as credential risk: {raw!r}"
            if p.name in _CRED_NAMES or p.suffix in _CRED_SUFFIXES:
                return True, f"credential-like file: {p}"
            for d in cred_dirs:
                if d == p or d in p.parents:
                    return True, f"path inside credential dir {d}: {p}"
        return False, ""
    except Exception as exc:  # fail closed
        return True, f"matcher error, failing closed: {exc!r}"


def is_mass_deletion(
    target_count: int, targets_glob: str | None, threshold: int
) -> tuple[bool, str]:
    try:
        if targets_glob:
            root = targets_glob.rstrip("/*")
            if any(tok in targets_glob for tok in ("/*", "/**", "~")) and root.count("/") <= 1:
                return True, f"delete targets a top-level tree: {targets_glob!r}"
        if target_count > threshold:
            return True, f"delete affects {target_count} items (> {threshold})"
        return False, ""
    except Exception as exc:
        return True, f"matcher error, failing closed: {exc!r}"


def is_financial(
    url: str | None, command: str | None, financial_domains: list[str]
) -> tuple[bool, str]:
    try:
        if url:
            parsed = urlparse(url)
            host = (parsed.hostname or "").lower()
            for d in financial_domains:
                if host == d or host.endswith("." + d):
                    return True, f"financial domain: {host}"
            if any(h in parsed.path for h in _FIN_PATH_HINTS):
                return True, f"financial endpoint: {url}"
        if command and any(b in command for b in _FIN_CLI_HINTS):
            return True, f"financial CLI: {command!r}"
        return False, ""
    except Exception as exc:
        return True, f"matcher error, failing closed: {exc!r}"
