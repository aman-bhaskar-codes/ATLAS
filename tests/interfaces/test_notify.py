"""Tests for notification interfaces."""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from atlas.infra.types import SafetyDecision, Tier, ToolRequest
from atlas.interfaces.notify import CliConfirmer, CompositeConfirmer, Notifier, NtfyNotifier


class FakeIds:
    def __init__(self) -> None:
        self._counter = 0

    def execution_id(self) -> Any:
        self._counter += 1
        return f"exec-{self._counter}"


def make_safety_decision(decision: str = "require_confirm") -> SafetyDecision:
    return SafetyDecision(
        decision=decision,
        tier=Tier.CONFIRM,
        reason="test",
        requires_sandbox=False,
    )


def make_tool_request() -> ToolRequest:
    return ToolRequest(
        correlation_id="test",
        tool="fs",
        operation="read",
    )


class TestNtfyNotifier:
    @pytest.fixture
    def mock_client(self) -> AsyncMock:
        return AsyncMock()

    @pytest.fixture
    def notifier(self, mock_client: AsyncMock) -> NtfyNotifier:
        ids = FakeIds()
        n = NtfyNotifier("test-topic", "http://localhost:8000/cb", ids)
        n._client = mock_client
        return n

    @pytest.mark.asyncio
    async def test_notify_posts_to_ntfy(self, notifier: NtfyNotifier, mock_client: AsyncMock) -> None:
        await notifier.notify("Test Title", "Test body", priority=4)
        mock_client.post.assert_called_once()
        call_args = mock_client.post.call_args
        assert call_args[0][0] == "https://ntfy.sh/test-topic"
        assert call_args[1]["headers"]["Title"] == "Test Title"
        assert call_args[1]["headers"]["Priority"] == "4"

    @pytest.mark.asyncio
    async def test_ask_returns_none_on_timeout(self, notifier: NtfyNotifier, mock_client: AsyncMock) -> None:
        mock_client.post = AsyncMock(return_value=MagicMock())
        result = await notifier.ask("Confirm?", "Please confirm", timeout_s=0.01)
        assert result is None

    @pytest.mark.asyncio
    async def test_close_calls_aclose(self, notifier: NtfyNotifier, mock_client: AsyncMock) -> None:
        await notifier.close()
        mock_client.aclose.assert_called_once()

    @pytest.mark.asyncio
    async def test_resolve_sets_future_result(self, notifier: NtfyNotifier, mock_client: AsyncMock) -> None:
        mock_client.post = AsyncMock(return_value=MagicMock())
        task = asyncio.create_task(notifier.ask("Confirm?", "Please confirm", timeout_s=1.0))
        await asyncio.sleep(0.01)
        for req_id in list(notifier._pending.keys()):
            notifier.resolve(req_id, True)
        result = await asyncio.wait_for(task, timeout=1.0)
        assert result is True


class TestCliConfirmer:
    @pytest.mark.asyncio
    async def test_confirm_returns_true_for_yes(self, monkeypatch: pytest.MonkeyPatch) -> None:
        confirmer = CliConfirmer()
        monkeypatch.setattr("builtins.input", lambda _: "y")
        req = make_tool_request()
        result = await confirmer.confirm("Confirm?", make_safety_decision(), req)
        assert result is True

    @pytest.mark.asyncio
    async def test_confirm_returns_false_for_no(self, monkeypatch: pytest.MonkeyPatch) -> None:
        confirmer = CliConfirmer()
        monkeypatch.setattr("builtins.input", lambda _: "n")
        req = make_tool_request()
        result = await confirmer.confirm("Confirm?", make_safety_decision(), req)
        assert result is False


class TestCompositeConfirmer:
    @pytest.fixture
    def mock_notifier(self) -> AsyncMock:
        return AsyncMock(spec=Notifier)

    @pytest.mark.asyncio
    async def test_uses_notifier_when_available(self, mock_notifier: AsyncMock) -> None:
        mock_notifier.ask.return_value = True
        cli = CliConfirmer()
        composite = CompositeConfirmer(mock_notifier, cli, timeout_s=5.0)
        req = make_tool_request()
        result = await composite.confirm("Confirm?", make_safety_decision(), req)
        assert result is True
        mock_notifier.ask.assert_called_once()

    @pytest.mark.asyncio
    async def test_falls_back_to_cli_when_no_notifier(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("builtins.input", lambda _: "y")
        cli = CliConfirmer()
        composite = CompositeConfirmer(None, cli, timeout_s=5.0)
        req = make_tool_request()
        result = await composite.confirm("Confirm?", make_safety_decision(), req)
        assert result is True

    @pytest.mark.asyncio
    async def test_notifier_timeout_fails_closed(self, mock_notifier: AsyncMock) -> None:
        mock_notifier.ask.return_value = None
        cli = CliConfirmer()
        composite = CompositeConfirmer(mock_notifier, cli, timeout_s=0.01)
        req = make_tool_request()
        result = await composite.confirm("Confirm?", make_safety_decision(), req)
        assert result is False
