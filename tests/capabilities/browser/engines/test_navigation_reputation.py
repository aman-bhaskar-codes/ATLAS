"""Tests for NavigationEngine URL reputation checking."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from atlas.capabilities.browser.domain.page import PageHandle, PageState
from atlas.capabilities.browser.engines.navigation import NavigationEngine
from atlas.capabilities.browser.errors import UnsafeURLError
from atlas.capabilities.browser.security.reputation import ReputationResult, ReputationVerdict
from atlas.infra.ids import CorrelationId


@pytest.mark.asyncio
async def test_goto_raises_unsafe_url_error_for_malicious() -> None:
    page_manager = MagicMock()
    provider = AsyncMock()
    page_manager.get_provider.return_value = (provider, "session_1", "tab_1")

    state_builder = AsyncMock()
    state_builder.build_state.return_value = MagicMock(spec=PageState)

    reputation_checker = AsyncMock()
    reputation_checker.check.return_value = ReputationResult(
        url="http://malicious.example.com",
        verdict=ReputationVerdict.MALICIOUS,
        reason="Known malicious URL",
        checked_by="safe_browsing",
    )

    engine = NavigationEngine(
        page_manager=page_manager,
        state_builder=state_builder,
        reputation_checker=reputation_checker,
    )

    handle = PageHandle(session_id="test", tab_id="test")
    url = "http://malicious.example.com"

    with pytest.raises(UnsafeURLError, match="malicious"):
        await engine.goto(handle, url, CorrelationId("cid_123"))

    reputation_checker.check.assert_called_once_with(url)
    provider.goto.assert_not_called()


@pytest.mark.asyncio
async def test_goto_proceeds_for_safe_url() -> None:
    page_manager = MagicMock()
    provider = AsyncMock()
    page_manager.get_provider.return_value = (provider, "session_1", "tab_1")

    expected_state = MagicMock(spec=PageState)
    state_builder = AsyncMock()
    state_builder.build_state.return_value = expected_state

    reputation_checker = AsyncMock()
    reputation_checker.check.return_value = ReputationResult(
        url="https://example.com",
        verdict=ReputationVerdict.SAFE,
        reason="No known threats",
        checked_by="safe_browsing",
    )

    engine = NavigationEngine(
        page_manager=page_manager,
        state_builder=state_builder,
        reputation_checker=reputation_checker,
    )

    handle = PageHandle(session_id="test", tab_id="test")
    url = "https://example.com"

    result = await engine.goto(handle, url, CorrelationId("cid_123"))

    assert result is expected_state
    reputation_checker.check.assert_called_once_with(url)
    provider.goto.assert_called_once_with("session_1", "tab_1", url)
    state_builder.build_state.assert_called_once_with(handle)


@pytest.mark.asyncio
async def test_goto_fail_open_for_unknown_reputation() -> None:
    page_manager = MagicMock()
    provider = AsyncMock()
    page_manager.get_provider.return_value = (provider, "session_1", "tab_1")

    expected_state = MagicMock(spec=PageState)
    state_builder = AsyncMock()
    state_builder.build_state.return_value = expected_state

    reputation_checker = AsyncMock()
    reputation_checker.check.return_value = ReputationResult(
        url="https://unknown.example.com",
        verdict=ReputationVerdict.UNKNOWN,
        reason="Unable to verify reputation",
        checked_by="none",
    )

    engine = NavigationEngine(
        page_manager=page_manager,
        state_builder=state_builder,
        reputation_checker=reputation_checker,
    )

    handle = PageHandle(session_id="test", tab_id="test")
    url = "https://unknown.example.com"

    result = await engine.goto(handle, url, CorrelationId("cid_123"))

    assert result is expected_state
    reputation_checker.check.assert_called_once_with(url)
    provider.goto.assert_called_once_with("session_1", "tab_1", url)
    state_builder.build_state.assert_called_once_with(handle)
