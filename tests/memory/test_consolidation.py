"""Tests for memory consolidation."""

from __future__ import annotations

import pytest

from atlas.memory.consolidation import _AUTO_APPLY_CONFIDENCE, _DUP_SIMILARITY, Consolidator
from atlas.memory.types import FactKind


class TestConsolidationConstants:
    def test_auto_apply_confidence_threshold(self) -> None:
        assert _AUTO_APPLY_CONFIDENCE == 0.8

    def test_dup_similarity_threshold(self) -> None:
        assert _DUP_SIMILARITY == 0.92


class TestRoughlySame:
    def test_identical_strings(self) -> None:
        assert Consolidator._roughly_same("hello world", "hello world") is True

    def test_different_strings(self) -> None:
        assert Consolidator._roughly_same("hello world", "foo bar") is False

    def test_empty_strings(self) -> None:
        assert Consolidator._roughly_same("", "hello") is False

    def test_high_overlap(self) -> None:
        assert Consolidator._roughly_same("hello world foo bar", "hello world foo baz") is True


class TestKind:
    def test_valid_kind(self) -> None:
        assert Consolidator._kind("preference") == FactKind.PREFERENCE

    def test_invalid_kind_defaults_to_fact(self) -> None:
        assert Consolidator._kind("invalid_kind") == FactKind.FACT

    def test_empty_string_defaults_to_fact(self) -> None:
        assert Consolidator._kind("") == FactKind.FACT


class TestExtractJson:
    def test_simple_json(self) -> None:
        result = Consolidator._extract_json('{"key": "value"}')
        assert result == '{"key": "value"}'

    def test_json_with_text_around(self) -> None:
        result = Consolidator._extract_json('Here is JSON: {"key": "value"} done')
        assert result == '{"key": "value"}'

    def test_no_json_raises(self) -> None:
        with pytest.raises(ValueError):
            Consolidator._extract_json("no json here")

    def test_nested_json(self) -> None:
        result = Consolidator._extract_json('{"outer": {"inner": "value"}}')
        assert result == '{"outer": {"inner": "value"}}'
