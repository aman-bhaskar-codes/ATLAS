"""Sensitivity classification for the frontmost app.

WHY here and now (even though C1 never leaves the machine): tagging ScreenState
as sensitive is what later lets the router BLOCK cloud vision (C5) on a banking
or password-manager window. Defining it at perception time keeps the rule in one
place.
"""

from __future__ import annotations

_SENSITIVE_BUNDLE_HINTS: frozenset[str] = frozenset({
    "1password", "bitwarden", "keychain", "lastpass", "dashlane",
    "bank", "banking", "wallet", "messages", "mail", "whatsapp",
    "signal", "telegram", "venmo", "paypal", "coinbase",
})


def is_sensitive_app(app_name: str | None) -> bool:
    if not app_name:
        return False
    lowered = app_name.lower()
    return any(hint in lowered for hint in _SENSITIVE_BUNDLE_HINTS)
