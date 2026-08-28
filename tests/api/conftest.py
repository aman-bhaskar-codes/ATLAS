"""Shared fixtures for API-layer HTTP contract tests.

These tests exercise the REAL FastAPI app over real HTTP paths (via
``httpx.ASGITransport``) with the app's REAL lifespan running — Atlas is built
and started exactly as in production — but with the four external dependencies
the startup/execution path would otherwise reach patched to deterministic local
values: the OpenRouter chat provider, the cloud embedder, the Docker-sandbox
probe, and the semantic cache. This mirrors ``tests/e2e/test_first_light.py`` so
the mocking surface stays consistent across the suite.

WHY run the real lifespan: every route reads ``app.state.atlas`` /
``app.state.event_store`` (see ``dependencies.py``), which only exist after the
lifespan runs. ``ASGITransport`` does not emit ASGI lifespan events, so we enter
``app.router.lifespan_context(app)`` explicitly. The result is a faithful
integration test of the wired application — not a hand-assembled stub — so a
drift between the pydantic response models and the bytes the frontend's zod
schemas parse fails CI here.
"""

from __future__ import annotations

import contextlib
import os
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from atlas.intelligence.contracts import Usage
from atlas.intelligence.providers.base import ProviderCompletion
from atlas.interfaces.api.app import create_app

# A well-formed reasoning-loop reply whose action is a final answer, so any
# background orchestrator run triggered by ``POST /tasks`` terminates cleanly
# under the mock instead of looping.
_FINAL_ANSWER_JSON = (
    '{"thought": "Done.", "confidence": 0.9, '
    '"action": {"kind": "final_answer", "tool": null, "operation": null, '
    '"args": {}, "final_text": "Completed the requested task."}}'
)


@contextlib.contextmanager
def _external_mocks(data_dir: Path) -> Iterator[None]:
    """Patch every external dependency the wired app touches on start/execute."""
    os.environ["ATLAS_DATA_DIR"] = str(data_dir)
    os.environ["ATLAS_ENV"] = "dev"
    with contextlib.ExitStack() as stack:
        # A dummy key guarantees the OpenRouter provider registers on any machine;
        # `complete` is patched below, so no request ever leaves the box.
        stack.enter_context(patch.dict(os.environ, {"OPENROUTER_API_KEY": "test-key"}))
        stack.enter_context(
            patch(
                "atlas.memory.embedder.CloudEmbedder.embed",
                new_callable=AsyncMock,
                return_value=[0.0] * 1024,
            )
        )
        stack.enter_context(
            patch(
                "atlas.intelligence.providers.openai_compatible.OpenAICompatibleProvider.complete",
                new_callable=AsyncMock,
                return_value=ProviderCompletion(
                    text=_FINAL_ANSWER_JSON,
                    usage=Usage(input_tokens=10, output_tokens=5, usd=0.0),
                ),
            )
        )
        stack.enter_context(
            patch(
                "atlas.safety.sandbox_docker.DockerSandbox.health",
                new_callable=AsyncMock,
                return_value=False,  # force the native sandbox
            )
        )
        stack.enter_context(
            patch(
                "atlas.intelligence.cache.SemanticCache.get",
                new_callable=AsyncMock,
                return_value=None,
            )
        )
        yield


@contextlib.asynccontextmanager
async def app_client(
    data_dir: Path,
    monkeypatch: pytest.MonkeyPatch | None = None,
    **env: str,
) -> AsyncIterator[tuple[FastAPI, AsyncClient]]:
    """A started app plus its client, exposing the app object itself.

    WHY this exists alongside the ``api_client`` fixture: some contracts can only
    be provoked through ``app.state`` (the rate limiter, the resolved version) or
    need an env var read at ``create_app()`` time — which a fixture body has
    already passed by the time the test function runs. Tests that need neither
    should use ``api_client``.

    ``monkeypatch`` is optional only so this can be called from a fixture; pass it
    whenever ``env`` is non-empty so the vars are undone afterwards.
    """
    if env:
        assert monkeypatch is not None, "pass monkeypatch when overriding env, or the vars leak"
        for key, value in env.items():
            monkeypatch.setenv(key, value)
    with _external_mocks(data_dir):
        app = create_app()
        async with app.router.lifespan_context(app):
            # raise_app_exceptions=False makes ASGITransport behave like the real
            # uvicorn server: an exception that escapes a route is rendered into a
            # JSON envelope and returned to the client instead of being re-raised
            # into the test. Contract tests must observe the bytes a browser would,
            # not the raw exception.
            #
            # Mapped domain exceptions (NotFoundError->404, DeniedError->403, ...)
            # are handled by per-type handlers on ExceptionMiddleware; anything
            # unmapped is caught by the request middleware and rendered as the 500
            # envelope. See errors.py for why there is no blanket Exception
            # handler. Note that a plain KeyError is NO LONGER a 404 — only
            # NotFoundError is.
            transport = ASGITransport(app=app, raise_app_exceptions=False)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                yield app, client


@pytest_asyncio.fixture
async def api_client(tmp_path: Path) -> AsyncIterator[AsyncClient]:
    """An httpx client bound to a fully started ATLAS app (externals mocked)."""
    async with app_client(tmp_path) as (_app, client):
        yield client
