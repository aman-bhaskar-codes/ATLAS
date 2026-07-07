"""Capability vocabulary — the routing currency.

WHY an enum + frozenset: routing is capability-based, never model-based. A
request declares REQUIRED capabilities; a model ADVERTISES a capability set;
selection is set-containment. This decouples 'what the task needs' from 'which
model exists today'.
"""

from __future__ import annotations

from enum import StrEnum


class Capability(StrEnum):
    PLANNING = "planning"
    REASONING = "reasoning"
    CODING = "coding"
    VISION = "vision"
    TOOL_CALLING = "tool_calling"
    STRUCTURED_OUTPUT = "structured_output"
    LONG_CONTEXT = "long_context"
    EMBEDDING = "embedding"
    CLASSIFICATION = "classification"
    SUMMARIZATION = "summarization"
    TRANSLATION = "translation"
    REFLECTION = "reflection"
    CONSENSUS = "consensus"
    JSON_GENERATION = "json_generation"
    STREAMING = "streaming"


CapabilitySet = frozenset[Capability]


def parse_capabilities(values: list[str]) -> CapabilitySet:
    out: set[Capability] = set()
    for v in values:
        try:
            out.add(Capability(v))
        except ValueError as exc:
            raise ValueError(f"unknown capability {v!r}") from exc
    return frozenset(out)
