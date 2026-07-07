"""Platform capability detection.

WHY centralized: macOS backends (AX, AppleScript) must degrade to a clean,
typed 'unsupported' result off-Darwin so the package imports and tests run on
Linux CI. Everything checks these flags rather than sprinkling sys.platform.
"""

from __future__ import annotations

import sys
from functools import cache


def is_macos() -> bool:
    return sys.platform == "darwin"


@cache
def has_pyobjc() -> bool:
    """True only if we're on macOS AND the PyObjC frameworks import.
    Cached because the import probe is not free and the answer is constant."""
    if not is_macos():
        return False
    try:
        import AppKit  # type: ignore[import-untyped] # noqa: F401
        import ApplicationServices  # type: ignore[import-untyped] # noqa: F401
    except ImportError:
        return False
    return True
