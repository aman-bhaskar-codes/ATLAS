"""Synthesis — merge SubTaskResults into one answer.

WHY a model call: concatenating specialist outputs produces a report, not an
answer. The synthesizer resolves overlap, notes contradictions between branches,
and answers the ORIGINAL request.

WHY a deterministic fallback: synthesis is the last step, so a model failure
here would throw away work that already succeeded. If the call fails we return a
structured digest of the raw outputs instead — degraded, but never empty.
"""

from __future__ import annotations

from atlas.infra.ids import CorrelationId
from atlas.infra.logging import get_logger
from atlas.infra.types import ModelCapability, ModelRequest, Tier
from atlas.intelligence.gateway import ModelGateway
from atlas.orchestration.agents.types import SubTaskResult, SubTaskStatus

_log = get_logger("atlas.orch.agents.synth")

_SYNTH_SYSTEM = (
    "You are the synthesizer for a multi-agent run. You are given the original request "
    "and each specialist's findings. Produce the final answer to the REQUEST.\n"
    "Rules: use only what the specialists reported; never invent facts. If specialists "
    "contradict each other, say so explicitly rather than silently picking one. If a "
    "subtask failed, state what is therefore unknown. No preamble, no meta-commentary "
    "about agents — just the answer."
)

_MAX_OUTPUT_CHARS = 6000  # per specialist, before the synthesis prompt


class Synthesizer:
    def __init__(self, gateway: ModelGateway, *, max_tokens: int = 2048) -> None:
        self._gw = gateway
        self._max_tokens = max_tokens

    async def synthesize(
        self,
        *,
        request: str,
        results: tuple[SubTaskResult, ...],
        correlation_id: CorrelationId,
    ) -> str:
        """Return the final answer. Never raises; never returns empty."""
        if not results:
            return "No subtask produced a result."

        digest = self._digest(results)
        succeeded = [r for r in results if r.ok and r.output]
        if not succeeded:
            # Nothing to synthesize — a model call here would only paraphrase
            # failures back at the user and cost a round trip.
            return digest

        try:
            resp = await self._gw.complete(
                ModelRequest(
                    correlation_id=correlation_id,
                    system=_SYNTH_SYSTEM,
                    prompt=f"REQUEST:\n{request}\n\nSPECIALIST FINDINGS:\n{digest}",
                    required_capabilities=frozenset({ModelCapability.REASONING}),
                    needs_deep_reasoning=True,
                    stakes_tier=Tier.AUTO,  # synthesis writes nothing
                    max_tokens=self._max_tokens,
                )
            )
            if text := str(resp.text).strip():
                return text
            _log.warning(
                "synthesis.empty_response",
                event_type="orchestration",
                correlation_id=str(correlation_id),
            )
        except Exception as exc:
            _log.warning(
                "synthesis.failed",
                event_type="orchestration",
                correlation_id=str(correlation_id),
                error=repr(exc),
            )
        return digest

    def _digest(self, results: tuple[SubTaskResult, ...]) -> str:
        """Deterministic rendering — also the fallback answer."""
        blocks: list[str] = []
        for r in results:
            header = f"[{r.subtask_id} · {r.role.value} · {r.status.value}]"
            if r.status is SubTaskStatus.SKIPPED:
                body = "Not attempted — a subtask it depended on failed."
            elif r.ok:
                body = r.output[:_MAX_OUTPUT_CHARS] or "(no output)"
            else:
                body = f"FAILED: {r.error or 'no error detail'}"
            blocks.append(f"{header}\n{body}")
        return "\n\n".join(blocks)
