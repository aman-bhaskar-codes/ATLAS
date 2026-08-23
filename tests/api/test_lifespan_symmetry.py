"""The lifespan must be symmetric: anything built gets closed.

THE BUG THIS PINS: the lifespan body ran straight from ``build()`` to ``yield``
with no ``try``. An ``@asynccontextmanager`` lifespan only executes the code after
its ``yield`` if startup REACHED the yield — so any failure in between (a
provider probe, the trigger engine, a broadcaster) left a fully constructed Atlas
behind: SQLite connections open, the ChromaDB client alive, the health-monitor
loop running, and no ``close()`` ever called. In a reload loop or a test suite
that is one leaked runtime per failed boot.

Second half of the same fix: teardown steps are individually isolated. A
broadcaster that throws on ``stop()`` must not prevent ``atlas.close()`` from
releasing the database — otherwise one cosmetic shutdown error leaks everything
underneath it.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from atlas.app import Atlas
from atlas.interfaces.api.app import create_app
from atlas.interfaces.api.websocket import EventBroadcaster
from tests.api.conftest import _external_mocks


def _close_spy() -> tuple[list[int], Callable[[Atlas], Awaitable[None]]]:
    """A stand-in for ``Atlas.close`` that counts calls and then calls through.

    A plain function, not a callable object: only functions implement the
    descriptor protocol, so only a function assigned to the class attribute gets
    ``self`` bound when the instance calls it.
    """
    original = Atlas.close
    calls: list[int] = []

    async def close(self: Atlas) -> None:
        calls.append(1)
        await original(self)

    return calls, close


async def test_startup_failure_still_closes_atlas(tmp_path: Path) -> None:
    """A raise between build() and yield must not leak the constructed runtime."""
    calls, spy = _close_spy()

    with _external_mocks(tmp_path):
        app = create_app()
        with (
            patch.object(Atlas, "close", spy),
            patch.object(Atlas, "start", AsyncMock(side_effect=RuntimeError("startup exploded"))),
            pytest.raises(RuntimeError, match="startup exploded"),
        ):
            async with app.router.lifespan_context(app):
                pytest.fail("startup should have raised before reaching the yield")

    assert calls == [1], "Atlas was built and then never closed — every handle it holds leaked"


async def test_broadcaster_stop_failure_does_not_block_close(tmp_path: Path) -> None:
    """Teardown steps are isolated, so a failing broadcaster cannot leak the DB."""
    calls, spy = _close_spy()

    with _external_mocks(tmp_path):
        app = create_app()
        with (
            patch.object(Atlas, "close", spy),
            patch.object(EventBroadcaster, "stop", AsyncMock(side_effect=RuntimeError("stop failed"))),
        ):
            async with app.router.lifespan_context(app):
                pass

    assert calls == [1], "a broadcaster that fails to stop prevented atlas.close()"


async def test_clean_shutdown_closes_exactly_once(tmp_path: Path) -> None:
    """The ordinary path: one close, not zero and not two."""
    calls, spy = _close_spy()

    with _external_mocks(tmp_path):
        app = create_app()
        with patch.object(Atlas, "close", spy):
            async with app.router.lifespan_context(app):
                assert app.state.atlas is not None

    assert calls == [1]
