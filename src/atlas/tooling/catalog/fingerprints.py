"""Canonical serialization + fingerprints.

WHY fingerprints at all (Part 2 §12-§14): the catalog must detect that a tool's
schema or metadata CHANGED without relying on timestamps or source goodwill.
Two fingerprints per tool:

* ``schema_fingerprint``    — over one JSON schema (input or output)
* ``definition_fingerprint``— over the tool's semantic surface (name,
  description, operations, schemas, version)

Stability requirement (§13): semantically identical JSON schemas whose KEY
ORDER differs must produce the SAME fingerprint. Canonicalization therefore
recursively sorts object keys while PRESERVING array order (JSON-schema arrays
such as ``required`` or ``enum`` are order-semantic). SHA-256 over the
canonical bytes.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from atlas.tooling.models.tool_definition import UniversalToolDefinition


def canonical_json(value: Any) -> str:
    """Deterministic JSON: object keys sorted recursively, arrays in order,
    no insignificant whitespace, floats via repr-stable encoding."""
    return json.dumps(_canonicalize(value), sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _canonicalize(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _canonicalize(v) for k, v in sorted(value.items(), key=lambda kv: str(kv[0]))}
    if isinstance(value, (list, tuple)):
        return [_canonicalize(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    # Pydantic models, enums, datetimes -> their JSON-native form.
    if hasattr(value, "model_dump"):
        return _canonicalize(value.model_dump(mode="json"))
    if hasattr(value, "value"):  # StrEnum / IntEnum members
        return _canonicalize(value.value)
    return str(value)


def schema_fingerprint(schema: dict[str, Any] | None) -> str:
    """SHA-256 over the canonical form of one JSON schema. Empty/None -> ''."""
    if not schema:
        return ""
    return hashlib.sha256(canonical_json(schema).encode("utf-8")).hexdigest()


def definition_fingerprint(definition: UniversalToolDefinition) -> str:
    """SHA-256 over the tool's semantic surface: identity, description,
    operations, schemas, and version — deliberately EXCLUDING runtime state
    (status, availability, health) so metadata changes are detectable
    independently of execution state (Part 2 §14)."""
    payload = {
        "id": definition.id,
        "version": definition.version,
        "description": definition.description,
        "operations": list(definition.operations),
        "input_schema": definition.input_schema.model_dump(mode="json"),
        "output_schema": definition.output_schema,
        "execution_type": definition.execution_type.value,
        "safety_tool": definition.safety_tool,
        "capability": definition.capability,
    }
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def schema_size_estimate(schema: dict[str, Any] | None) -> tuple[int, int]:
    """(bytes, estimated_tokens) for model-surface budgeting (Part 2 §74).
    Token estimate is the crude bytes/4 heuristic — no tokenizer dependency."""
    if not schema:
        return 0, 0
    raw = canonical_json(schema).encode("utf-8")
    return len(raw), max(1, len(raw) // 4)
