"""Fingerprint tests (Part 2 §13/§14/§62): canonical, order-stable hashing."""

from __future__ import annotations

from atlas.tooling.catalog.fingerprints import (
    canonical_json,
    definition_fingerprint,
    schema_fingerprint,
)
from tests.tooling.catalog_helpers import make_definition


def test_same_schema_different_key_order_same_fingerprint() -> None:
    schema_a = {
        "type": "object",
        "properties": {"query": {"type": "string"}, "limit": {"type": "integer"}},
    }
    schema_b = {
        "properties": {"limit": {"type": "integer"}, "query": {"type": "string"}},
        "type": "object",
    }
    assert schema_fingerprint(schema_a) == schema_fingerprint(schema_b)


def test_nested_key_reordering_same_fingerprint() -> None:
    schema_a = {"properties": {"a": {"enum": ["x"], "type": "string"}}, "type": "object"}
    schema_b = {"type": "object", "properties": {"a": {"type": "string", "enum": ["x"]}}}
    assert schema_fingerprint(schema_a) == schema_fingerprint(schema_b)


def test_array_order_is_semantic_and_changes_fingerprint() -> None:
    schema_a = {"type": "object", "required": ["a", "b"]}
    schema_b = {"type": "object", "required": ["b", "a"]}
    assert schema_fingerprint(schema_a) != schema_fingerprint(schema_b)


def test_changed_schema_different_fingerprint() -> None:
    schema_a = {"type": "object", "properties": {"q": {"type": "string"}}}
    schema_b = {"type": "object", "properties": {"q": {"type": "string"}, "n": {"type": "integer"}}}
    assert schema_fingerprint(schema_a) != schema_fingerprint(schema_b)


def test_empty_schema_has_empty_fingerprint() -> None:
    assert schema_fingerprint(None) == ""
    assert schema_fingerprint({}) == ""


def test_canonical_json_is_deterministic() -> None:
    assert canonical_json({"b": 1, "a": [2, {"z": 1, "y": 2}]}) == canonical_json({"a": [2, {"y": 2, "z": 1}], "b": 1})


def test_definition_fingerprint_tracks_semantic_surface() -> None:
    definition = make_definition("filesystem", operations=("read", "write"), description="files")
    base = definition_fingerprint(definition)
    assert (
        definition_fingerprint(make_definition("filesystem", operations=("read", "write"), description="files")) == base
    )

    # description change -> new fingerprint
    assert (
        definition_fingerprint(make_definition("filesystem", operations=("read", "write"), description="file tool"))
        != base
    )
    # operation change -> new fingerprint
    assert definition_fingerprint(make_definition("filesystem", operations=("read",), description="files")) != base
    # version change -> new fingerprint

    bumped = definition.model_copy(update={"version": "2"})
    assert definition_fingerprint(bumped) != base
