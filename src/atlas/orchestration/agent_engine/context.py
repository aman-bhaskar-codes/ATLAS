"""Agent-run context assembly + rehydration (M0.5).

WHY this module: the M0.2 engine takes a ``list[Message]`` in and returns an
append-only ``AgentEngineResult`` trace. Two message-shaped operations sit either
side of that loop and belong together — apart from both the loop (which should
not know how a run is seeded or resumed) and the store (which should not know the
message protocol):

  - SEEDING a run's opening context (system + user request [+ prior history]);
  - REHYDRATING a persisted run into the exact conversation it produced, so a
    later process can CONTINUE it.

These are pure functions over ``intelligence.contracts`` — no I/O, no engine, no
store — which is what lets the substrate be resumable without coupling the loop
to persistence. Reconstruction is faithful: the model's tool-call arguments are
rebuilt in ATLAS's ``{operation, args}`` envelope (what ``engine._decode_arguments``
reads back), and tool results are re-serialized with the SAME
``serialize_tool_payload`` the engine used live — so the rebuilt TOOL turns match
byte-for-byte.

Unlike the engine's own transient convo (which returns before appending the
final, tool-less answer), the reconstructed conversation INCLUDES the model's
final assistant answer — it is the complete transcript, which is what a resume
needs the model to see.
"""

from __future__ import annotations

from collections.abc import Sequence

from atlas.infra.types import ProviderToolCall
from atlas.intelligence.contracts import Message, Role
from atlas.orchestration.agent_engine.records import AgentEngineResult, serialize_tool_payload


def seed_messages(
    system_prompt: str,
    user_request: str,
    *,
    history: Sequence[Message] = (),
) -> list[Message]:
    """Build the opening message list for a fresh run: an optional SYSTEM turn,
    any prior conversation ``history``, then the USER request. This is the
    message-oriented seam the engine consumes (``AgentEngine.run(messages=...)``),
    distinct from the legacy string-oriented ``orchestration.context_builder``.
    """
    messages: list[Message] = []
    if system_prompt.strip():
        messages.append(Message(role=Role.SYSTEM, content=system_prompt))
    messages.extend(history)
    messages.append(Message(role=Role.USER, content=user_request))
    return messages


def reconstruct_conversation(seed: Sequence[Message], result: AgentEngineResult) -> list[Message]:
    """Rebuild the full conversation a run produced, from its seed + result trace.

    For each step: replay the model's ASSISTANT turn (its text plus the tool calls
    it chose, each rebuilt as a ``ProviderToolCall`` carrying the ``{operation, args}``
    envelope the engine decodes), then one TOOL turn per call serialized exactly as
    the engine fed it back. The result is a conversation the engine could have
    produced itself — ready to hand to the model again.
    """
    convo: list[Message] = list(seed)
    for step in result.steps:
        tool_calls = tuple(
            ProviderToolCall(
                id=rec.call_id,
                name=rec.tool,
                arguments={"operation": rec.operation, "args": rec.args},
            )
            for rec in step.tool_calls
        )
        convo.append(Message(role=Role.ASSISTANT, content=step.assistant_text, tool_calls=tool_calls))
        for rec in step.tool_calls:
            convo.append(
                Message(
                    role=Role.TOOL,
                    content=serialize_tool_payload(ok=rec.ok, output=rec.output, error=rec.error),
                    tool_call_id=rec.call_id,
                    name=rec.tool,
                )
            )
    return convo


def continue_messages(
    seed: Sequence[Message],
    result: AgentEngineResult,
    next_user_request: str,
) -> list[Message]:
    """Rehydrate a finished run and append a new USER turn — the message list to
    pass back into ``AgentEngine.run(...)`` to CONTINUE the same run in a new turn.
    """
    convo = reconstruct_conversation(seed, result)
    convo.append(Message(role=Role.USER, content=next_user_request))
    return convo
