"""Versioned prompt templates.

WHY: named templates with versions allow us to track prompt changes and
gate them via eval in Phase 10.
"""

from __future__ import annotations

from dataclasses import dataclass

from atlas.intelligence.prompt.blocks import BlockKind, PromptBlock


@dataclass(frozen=True)
class TemplateDef:
    version: str
    blocks: list[PromptBlock]


TEMPLATES: dict[str, TemplateDef] = {
    "default": TemplateDef(
        version="v1.0",
        blocks=[
            PromptBlock(
                kind=BlockKind.SYSTEM,
                body="You are ATLAS, an elite autonomous engineering system.",
            ),
            PromptBlock(
                kind=BlockKind.SAFETY,
                body="Do not execute irreversible actions without confirmation.",
            ),
        ]
    )
}
