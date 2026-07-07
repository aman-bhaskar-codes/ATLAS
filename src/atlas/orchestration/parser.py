"""Response parser — model text -> typed Thought + Action.

WHY fail-closed to ask_user: if the model emits unparseable output, the SAFE
degradation is to stop and ask the human, never to guess an action. A parser
that invents a tool call from garbage is a safety hole.
"""

from __future__ import annotations

import json

from atlas.orchestration.types import Action, ActionKind, Thought


class ResponseParser:
    def parse(self, text: str, step: int) -> tuple[Thought, Action]:
        try:
            data = json.loads(self._json(text))
            thought = Thought(
                step=step, content=str(data.get("thought", "")),
                confidence=float(data.get("confidence", 0.5)),
            )
            a = data.get("action", {})
            action = Action(
                step=step, kind=self._kind(a.get("kind")),
                tool=a.get("tool"), operation=a.get("operation"),
                args=dict(a.get("args", {})), final_text=a.get("final_text"),
            )
            return thought, action
        except (json.JSONDecodeError, ValueError, TypeError):
            return (
                Thought(step=step, content="unparseable model output", confidence=0.0),
                Action(step=step, kind="ask_user",
                       final_text="I couldn't produce a clear next step. Can you clarify?"),
            )

    @staticmethod
    def _kind(raw: object) -> ActionKind:
        s = str(raw)
        if s in ("tool_call", "final_answer", "ask_user", "noop"):
            return s  # type: ignore[return-value]
        return "ask_user"

    @staticmethod
    def _json(text: str) -> str:
        s, e = text.find("{"), text.rfind("}")
        if s == -1 or e == -1:
            raise ValueError("no JSON")
        return text[s : e + 1]
