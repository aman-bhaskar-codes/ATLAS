"""Prompt builder — deterministic ReAct step prompts.

WHY a dedicated builder: the reasoning loop must produce PARSEABLE output every
step. Centralizing the output contract here (and nowhere else) means the parser
and the prompt can never drift. The critique hook (Phase 4.5) will extend the
suffix; the base contract stays stable.
"""

from __future__ import annotations

from atlas.orchestration.types import Observation, Thought

_OUTPUT_CONTRACT = (
    "Respond with ONLY JSON for ONE step:\n"
    '{"thought":str,"confidence":0.0-1.0,'
    '"action":{"kind":"tool_call|final_answer|ask_user|noop",'
    '"tool":str|null,"operation":str|null,"args":object,"final_text":str|null}}'
)


class PromptBuilder:
    def build_step_prompt(
        self, *, context: str, goal: str,
        history: list[tuple[Thought, Observation | None]], step: int,
    ) -> str:
        lines = [context, f"\nGOAL: {goal}", f"STEP: {step}", "\nHISTORY:"]
        for t, o in history:
            lines.append(f"  thought: {t.content}")
            if o is not None:
                status = "ok" if o.ok else f"error: {o.error}"
                lines.append(f"  observation: {status} {str(o.content)[:300]}")
        lines.append("\n" + _OUTPUT_CONTRACT)
        return "\n".join(lines)
