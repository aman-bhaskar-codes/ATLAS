"""Tests for checkpoint manager."""

from __future__ import annotations

from pathlib import Path

import pytest

from atlas.orchestration.managers.checkpoint import Checkpoint, FileCheckpointStore


class TestCheckpoint:
    def test_is_dict_subclass(self) -> None:
        cp = Checkpoint()
        assert isinstance(cp, dict)

    def test_can_set_get_items(self) -> None:
        cp: Checkpoint = Checkpoint()
        cp["state"] = "running"
        cp["retries"] = 3
        assert cp["state"] == "running"
        assert cp["retries"] == 3

    def test_from_dict(self) -> None:
        cp: Checkpoint = Checkpoint({"key": "value"})
        assert cp["key"] == "value"


class TestFileCheckpointStore:
    @pytest.fixture
    def store(self, tmp_path: Path) -> FileCheckpointStore:
        return FileCheckpointStore(tmp_path)

    @pytest.mark.asyncio
    async def test_save_creates_file(self, store: FileCheckpointStore, tmp_path: Path) -> None:
        cp: Checkpoint = Checkpoint({"state": "running", "step": 5})
        await store.save("task-123", cp)
        assert (tmp_path / "checkpoints" / "task-123.json").exists()

    @pytest.mark.asyncio
    async def test_save_and_load_roundtrip(self, store: FileCheckpointStore) -> None:
        cp: Checkpoint = Checkpoint({"state": "paused", "data": [1, 2, 3]})
        await store.save("task-456", cp)
        loaded = await store.load("task-456")
        assert loaded is not None
        assert loaded["state"] == "paused"
        assert loaded["data"] == [1, 2, 3]

    @pytest.mark.asyncio
    async def test_load_nonexistent_returns_none(self, store: FileCheckpointStore) -> None:
        result = await store.load("nonexistent")
        assert result is None

    @pytest.mark.asyncio
    async def test_save_overwrites(self, store: FileCheckpointStore) -> None:
        cp1: Checkpoint = Checkpoint({"version": 1})
        cp2: Checkpoint = Checkpoint({"version": 2})
        await store.save("task-789", cp1)
        await store.save("task-789", cp2)
        loaded = await store.load("task-789")
        assert loaded is not None
        assert loaded["version"] == 2

    @pytest.mark.asyncio
    async def test_save_with_complex_types(self, store: FileCheckpointStore) -> None:
        cp: Checkpoint = Checkpoint({"meta": {"nested": True}, "tags": ["a", "b"]})
        await store.save("task-complex", cp)
        loaded = await store.load("task-complex")
        assert loaded is not None
        assert loaded["meta"] == {"nested": True}
        assert loaded["tags"] == ["a", "b"]
