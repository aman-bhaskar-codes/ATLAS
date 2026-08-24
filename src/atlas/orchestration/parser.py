"""Response parser — model text -> typed Thought + Action.

WHY fail-closed to ask_user: if the model emits unparseable output, the SAFE
degradation is to stop and ask the human, never to guess an action. A parser
that invents a tool call from garbage is a safety hole.
"""

from __future__ import annotations

import json

from atlas.infra.logging import get_logger
from atlas.orchestration.types import Action, ActionKind, Thought

_log = get_logger("atlas.orch.parser")


class ResponseParser:
    def parse(self, text: str, step: int) -> tuple[Thought, Action]:
        try:
            data = json.loads(self._json(text))
            thought = Thought(
                step=step,
                content=str(data.get("thought", "")),
                confidence=float(data.get("confidence", 0.5)),
            )
            a = data.get("action", {})
            action = Action(
                step=step,
                kind=self._kind(a.get("kind")),
                tool=a.get("tool"),
                operation=a.get("operation"),
                args=dict(a.get("args") or {}),
                final_text=a.get("final_text"),
            )
            return thought, action
        except (json.JSONDecodeError, ValueError, TypeError) as exc:
            _log.error("parser.failed", error=str(exc), raw_text=text)
            return (
                Thought(step=step, content="unparseable model output", confidence=0.0),
                Action(step=step, kind="ask_user", final_text="I couldn't produce a clear next step. Can you clarify?"),
            )

    @staticmethod
    def _kind(raw: object) -> ActionKind:
        s = str(raw)
        if s in ("tool_call", "final_answer", "ask_user", "noop"):
            return s  # type: ignore[return-value]
        return "ask_user"

    @staticmethod
    def _json(text: str) -> str:
        """Extract the last valid JSON object from model output.

        WHY search backwards: qwen3:4b writes verbose chain-of-thought that
        contains pseudo-JSON with // comments, partial JSON snippets, etc.
        The real answer JSON is always at the END. Searching from the end
        backwards for a balanced { → } pair that actually parses avoids
        picking up junk from the thinking preamble.
        """
        import re

        # Strip <think>...</think> blocks (qwen3 thinking mode remnants)
        cleaned = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)

        # Try from the LAST closing brace backwards
        e = len(cleaned) - 1
        while e >= 0:
            e = cleaned.rfind("}", 0, e + 1)
            if e == -1:
                break
            # Find matching opening brace by counting depth
            depth = 0
            for i in range(e, -1, -1):
                if cleaned[i] == "}":
                    depth += 1
                elif cleaned[i] == "{":
                    depth -= 1
                if depth == 0:
                    candidate = cleaned[i : e + 1]
                    try:
                        json.loads(candidate)
                        return candidate
                    except json.JSONDecodeError:
                        break  # this pair didn't parse, try an earlier '}'
            e -= 1

        raise ValueError("no valid JSON object found")
