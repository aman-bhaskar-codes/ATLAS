"""Typed prompt blocks — never concatenate strings by hand.

WHY typed blocks: a prompt is a STRUCTURED document (system + safety + memory +
context + fewshot + task), not a string. Typing each block lets us order,
budget, version, and measure them. The compiler renders blocks -> messages
deterministically.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum


class BlockKind(IntEnum):
    SYSTEM = 0
    SAFETY = 1
    DEVELOPER = 2
    TOOLS = 3
    MEMORY = 4
    CONTEXT = 5
    FEWSHOT = 6
    TASK = 7


@dataclass(frozen=True)
class PromptBlock:
    kind: BlockKind
    body: str
