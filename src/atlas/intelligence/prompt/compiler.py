"""Prompt compiler — blocks -> ordered, budgeted messages.

WHY deterministic order by BlockKind: reproducibility. WHY budget-aware: the
compiler trims the most negotiable blocks (fewshot, extra context) first, never
system/safety. This is the ONE place prompts are assembled — parser and prompt
can't drift because both live under intelligence/.
"""

from __future__ import annotations

from collections.abc import Sequence

from atlas.intelligence.contracts import Message, Role
from atlas.intelligence.prompt.blocks import BlockKind, PromptBlock


class PromptCompiler:
    def __init__(self, token_budget: int = 6000) -> None:
        self._budget = token_budget

    @staticmethod
    def _tok(s: str) -> int:
        return max(1, len(s) // 4)

    def compile(self, blocks: Sequence[PromptBlock], user_task: str) -> list[Message]:
        ordered = sorted(blocks, key=lambda b: b.kind)
        system_parts: list[str] = []
        used = 0
        for b in ordered:
            if not b.body.strip():
                continue
            cost = self._tok(b.body)
            # never trim SYSTEM/SAFETY; trim negotiable blocks if over budget
            if used + cost > self._budget and b.kind >= BlockKind.FEWSHOT:
                continue
            system_parts.append(b.body)
            used += cost
        return [
            Message(role=Role.SYSTEM, content="\n\n".join(system_parts)),
            Message(role=Role.USER, content=user_task),
        ]
