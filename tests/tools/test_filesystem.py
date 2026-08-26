"""Tests for filesystem tool."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest

from atlas.tools.filesystem import FilesystemTool


class TestFilesystemToolDryRun:
    @pytest.fixture
    def tool(self) -> FilesystemTool:
        return FilesystemTool(
            read_globs=["/tmp/*"],
            write_globs=["/tmp/*"],
            sandbox=AsyncMock(),
        )

    def test_dry_run_delete(self, tool: FilesystemTool) -> None:
        result = tool.dry_run({"operation": "delete", "path": "/tmp"})
        assert "DELETE" in result
        assert "/tmp" in result

    def test_dry_run_write(self, tool: FilesystemTool) -> None:
        result = tool.dry_run({"operation": "write", "path": "/tmp/file.txt", "content": "hello"})
        assert "WRITE" in result
        assert "5 bytes" in result

    def test_dry_run_read(self, tool: FilesystemTool) -> None:
        result = tool.dry_run({"operation": "read", "path": "/tmp/file.txt"})
        assert "READ" in result
        assert "/tmp/file.txt" in result

    def test_dry_run_search(self, tool: FilesystemTool) -> None:
        result = tool.dry_run({"operation": "search", "path": "/tmp", "query": "test"})
        assert "SEARCH" in result
        assert "test" in result

    def test_dry_run_unknown_op(self, tool: FilesystemTool) -> None:
        result = tool.dry_run({"operation": "unknown"})
        assert "unknown" in result


class TestCountDeleteTargets:
    @pytest.fixture
    def tool(self) -> FilesystemTool:
        return FilesystemTool(
            read_globs=["/tmp/*"],
            write_globs=["/tmp/*"],
            sandbox=AsyncMock(),
        )

    def test_count_file(self, tool: FilesystemTool, tmp_path: Any) -> None:
        file = tmp_path / "file.txt"
        file.write_text("content")
        count = tool._count_delete_targets(str(file))
        assert count == 1

    def test_count_directory(self, tool: FilesystemTool, tmp_path: Any) -> None:
        dir_path = tmp_path / "subdir"
        dir_path.mkdir()
        (dir_path / "file1.txt").write_text("a")
        (dir_path / "file2.txt").write_text("b")
        count = tool._count_delete_targets(str(dir_path))
        assert count == 2

    def test_count_nonexistent(self, tool: FilesystemTool) -> None:
        count = tool._count_delete_targets("/nonexistent/path")
        assert count == 0
