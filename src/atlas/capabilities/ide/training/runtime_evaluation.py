"""Bounded local inference evaluation without executing predicted tools.

Gold tool results are replayed only after exact tool choices. Gold assistant
answers are never included in a prediction prompt. Incorrect choices stop that
case immediately; no fabricated observations authorize a different action.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any
from uuid import uuid4

from atlas.capabilities.ide.training.datasets import DatasetError, TrainingExample, contains_sensitive
from atlas.infra.ids import CorrelationId
from atlas.infra.types import CostPolicy, NetworkPolicy, PrivacyClass
from atlas.intelligence.capabilities import Capability
from atlas.intelligence.contracts import Constraints, InferenceRequest, Message, Role


def _choices(calls: Any) -> list[tuple[str, dict[str, Any]]]:
    return [(call.name, call.arguments) for call in calls]


async def predict(gateway: Any, gold: Sequence[TrainingExample], model_id: str, provider: str) -> list[dict[str, Any]]:
    if not 1 <= len(gold) <= 20 or any(sum(m.role == Role.ASSISTANT for m in row.messages) > 16 for row in gold):
        raise DatasetError("runtime evaluation allows 1-20 held-out cases with at most 16 assistant turns each")
    predictions: list[dict[str, Any]] = []
    for row in gold:
        history: list[Message] = []
        identifiers: dict[str, str] = {}
        calls: list[dict[str, Any]] = []
        final_text = ""
        safe = True
        for message in row.messages:
            if message.role == Role.ASSISTANT:
                response = await gateway.infer(
                    InferenceRequest(
                        correlation_id=CorrelationId(uuid4().hex),
                        messages=tuple(history),
                        tools=row.tools,
                        required_capabilities=frozenset({Capability.TOOL_CALLING}),
                        max_tokens=512,
                        temperature=0,
                        constraints=Constraints(
                            pinned_model=model_id,
                            require_pinned_model=True,
                            privacy_class=PrivacyClass.SECRET,
                            cost_policy=CostPolicy.ZERO_COST,
                            network_policy=NetworkPolicy.OFFLINE,
                        ),
                    )
                )
                if (
                    response.model_id != model_id
                    or response.provider != provider
                    or response.fell_back
                    or response.truncated
                ):
                    raise DatasetError("runtime evaluation refused a fallback, truncated response, or model mismatch")
                calls.extend(call.model_dump(exclude={"thought_signature"}) for call in response.tool_calls)
                final_text = response.text
                if contains_sensitive(final_text) or contains_sensitive(calls):
                    safe = False
                    break
                if _choices(response.tool_calls) != _choices(message.tool_calls):
                    safe = not bool(response.tool_calls)
                    break
                identifiers.update(
                    {
                        expected.id: actual.id
                        for expected, actual in zip(message.tool_calls, response.tool_calls, strict=True)
                    }
                )
                history.append(Message(role=Role.ASSISTANT, content=response.text, tool_calls=response.tool_calls))
            elif message.role == Role.TOOL:
                history.append(message.model_copy(update={"tool_call_id": identifiers[message.tool_call_id or ""]}))
            else:
                history.append(message)
        predictions.append(
            {"case_id": row.identity, "tool_calls": calls, "final_text": final_text, "policy_safe": safe}
        )
    return predictions
