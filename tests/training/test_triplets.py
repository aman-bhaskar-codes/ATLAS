"""Tests for training triplets."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from atlas.training.triplets import (
    _NEGATIVE_LABELS,
    _POSITIVE_LABELS,
    _STOPWORDS,
    _tokens,
    mine_triplets,
)


class TestConstants:
    def test_positive_labels(self) -> None:
        assert "correct" in _POSITIVE_LABELS

    def test_negative_labels(self) -> None:
        assert "incorrect" in _NEGATIVE_LABELS
        assert "wrong_source" in _NEGATIVE_LABELS

    def test_stopwords(self) -> None:
        assert "the" in _STOPWORDS
        assert "and" in _STOPWORDS


class TestTokens:
    def test_extracts_words(self) -> None:
        result = _tokens("hello world test")
        assert "hello" in result
        assert "world" in result

    def test_removes_stopwords(self) -> None:
        result = _tokens("the quick brown fox")
        assert "the" not in result
        assert "quick" in result

    def test_removes_short_words(self) -> None:
        result = _tokens("a ab abc abcd")
        assert "a" not in result
        assert "ab" not in result
        assert "abc" in result

    def test_lowercase(self) -> None:
        result = _tokens("HELLO World")
        assert "hello" in result
        assert "world" in result


class TestMineTriplets:
    @pytest.fixture
    def mock_resolver(self) -> AsyncMock:
        resolver = AsyncMock()
        resolver.get_chunk = AsyncMock(
            side_effect=lambda chunk_id: (type("Chunk", (), {"content": f"content_{chunk_id}"}),)
        )
        return resolver

    @pytest.mark.asyncio
    async def test_empty_pairs(self, mock_resolver: AsyncMock) -> None:
        result = await mine_triplets([], mock_resolver)
        assert result.triplets == ()
        assert result.pairs_seen == 0

    @pytest.mark.asyncio
    async def test_positive_pair(self, mock_resolver: AsyncMock) -> None:
        pairs = [
            {"query": "test query", "chunk_id": "chunk1", "label": "correct"},
        ]
        result = await mine_triplets(pairs, mock_resolver)
        assert result.pairs_seen == 1

    @pytest.mark.asyncio
    async def test_ignores_unknown_labels(self, mock_resolver: AsyncMock) -> None:
        pairs = [
            {"query": "test", "chunk_id": "chunk1", "label": "unknown"},
        ]
        result = await mine_triplets(pairs, mock_resolver)
        assert result.triplets == ()

    @pytest.mark.asyncio
    async def test_creates_triplet_with_negative(self, mock_resolver: AsyncMock) -> None:
        pairs = [
            {"query": "test query", "chunk_id": "chunk1", "label": "correct"},
            {"query": "test query", "chunk_id": "chunk2", "label": "incorrect"},
        ]
        result = await mine_triplets(pairs, mock_resolver)
        assert len(result.triplets) >= 1
        assert result.triplets[0].anchor == "test query"

    @pytest.mark.asyncio
    async def test_max_triplets_limit(self, mock_resolver: AsyncMock) -> None:
        pairs = []
        for i in range(10):
            pairs.extend(
                [
                    {"query": f"query_{i}", "chunk_id": f"pos_{i}", "label": "correct"},
                    {"query": f"query_{i}", "chunk_id": f"neg_{i}", "label": "incorrect"},
                    {"query": f"query_{i}", "chunk_id": f"neg2_{i}", "label": "incorrect"},
                ]
            )
        result = await mine_triplets(pairs, mock_resolver, max_triplets=5)
        assert len(result.triplets) <= 5
