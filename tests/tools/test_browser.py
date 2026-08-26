"""Tests for browser tool."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest

from atlas.tools.browser import BrowserTool


class TestBrowserToolDryRun:
    @pytest.fixture
    def mock_platform(self) -> AsyncMock:
        return AsyncMock()

    @pytest.fixture
    def mock_ids(self) -> Any:
        class FakeIds:
            def generate(self, _: str) -> str:
                return "test-cid"

        return FakeIds()

    @pytest.fixture
    def tool(self, mock_platform: AsyncMock, mock_ids: Any) -> BrowserTool:
        return BrowserTool(platform=mock_platform, ids=mock_ids)

    def test_dry_run_research(self, tool: BrowserTool) -> None:
        result = tool.dry_run({"operation": "research", "seed_url": "https://example.com"})
        assert "CRAWL" in result
        assert "https://example.com" in result

    def test_dry_run_goto(self, tool: BrowserTool) -> None:
        result = tool.dry_run({"operation": "goto", "url": "https://example.com"})
        assert "NAVIGATE" in result
        assert "https://example.com" in result

    def test_dry_run_extract(self, tool: BrowserTool) -> None:
        result = tool.dry_run({"operation": "extract"})
        assert "EXTRACT" in result

    def test_dry_run_click(self, tool: BrowserTool) -> None:
        result = tool.dry_run({"operation": "click", "selector": "#button"})
        assert "CLICK" in result
        assert "#button" in result

    def test_dry_run_unknown_op(self, tool: BrowserTool) -> None:
        result = tool.dry_run({"operation": "unknown"})
        assert "unknown" in result
