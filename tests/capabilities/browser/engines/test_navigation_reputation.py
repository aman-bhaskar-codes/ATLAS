"""Tests for NavigationEngine URL reputation checking."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from atlas.capabilities.browser.domain.page import PageHandle, PageState
from atlas.capabilities.browser.engines.navigation import NavigationEngine
from atlas.capabilities.browser.errors import UnsafeURLError
from atlas.capabilities.browser.security.egress import EgressPolicy
from atlas.capabilities.browser.security.reputation import ReputationResult, ReputationVerdict
from atlas.infra.ids import CorrelationId


async def public_dns(host: str) -> list[str]:
    return ["8.8.8.8"]


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
        egress_policy=EgressPolicy(resolver=public_dns),
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
        egress_policy=EgressPolicy(resolver=public_dns),
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
        egress_policy=EgressPolicy(resolver=public_dns),
    )

    handle = PageHandle(session_id="test", tab_id="test")
    url = "https://unknown.example.com"

    result = await engine.goto(handle, url, CorrelationId("cid_123"))

    assert result is expected_state
    reputation_checker.check.assert_called_once_with(url)
    provider.goto.assert_called_once_with("session_1", "tab_1", url)
    state_builder.build_state.assert_called_once_with(handle)


async def test_navigation_denies_private_dns_before_provider_or_reputation() -> None:
    async def private_dns(host: str) -> list[str]:
        return ["10.0.0.1"]

    manager = MagicMock()
    reputation = AsyncMock()
    engine = NavigationEngine(manager, AsyncMock(), reputation, EgressPolicy(resolver=private_dns))
    with pytest.raises(UnsafeURLError):
        await engine.goto(PageHandle(session_id="test", tab_id="test"), "https://public.test/", CorrelationId("cid"))
    manager.get_provider.assert_not_called()
    reputation.check.assert_not_called()


async def test_navigation_denies_provider_without_request_interception() -> None:
    from atlas.capabilities.browser.errors import NavigationError
    from atlas.capabilities.browser.providers.capabilities import ProviderCapabilities

    manager = MagicMock()
    provider = AsyncMock()
    provider.capabilities = ProviderCapabilities(request_interception=False)
    manager.get_provider.return_value = (provider, "session", "tab")
    engine = NavigationEngine(manager, AsyncMock(), egress_policy=EgressPolicy(resolver=public_dns))
    with pytest.raises(NavigationError, match="egress interception"):
        await engine.goto(PageHandle(session_id="session", tab_id="tab"), "https://public.test/", CorrelationId("cid"))
    provider.goto.assert_not_called()
