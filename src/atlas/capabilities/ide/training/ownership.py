"""Native process ownership, held until governed execution actually settles.

An OS lock has no heartbeat expiry that could admit overlapping weight jobs.
The production lock is outside the editable workspace in application state.
"""

from __future__ import annotations

import os
from pathlib import Path

from atlas.capabilities.ide.training.datasets import DatasetError


def claim(path: Path) -> int | None:
    try:
        import fcntl
    except ImportError as exc:
        raise DatasetError("native training ownership is unavailable on this platform") from exc
    descriptor = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(descriptor)
        return None
    except BaseException:
        os.close(descriptor)
        raise
    return descriptor


def release(descriptor: int) -> None:
    os.close(descriptor)
