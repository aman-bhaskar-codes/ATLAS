// frontend/features/agent-runs/useAgentRunStream.ts
//
// Live SSE client for a run's durable trace (GET /agent/runs/{id}/stream). The
// backend frames named events — `connected`, `agent_event` (with `id: {sequence}`),
// `heartbeat`, `stream_closed` — and resumes from the `Last-Event-ID` header.
//
// WHY native EventSource reconnection (no manual close+reopen): resume is driven by
// `Last-Event-ID`, which the browser tracks from each frame's `id:` line and resends
// automatically on ITS OWN reconnect. Manually closing and constructing a new
// EventSource would drop that cursor and replay the whole snapshot. So on a
// transient error we let EventSource retry itself; we only close deliberately on
// `stream_closed` (terminal) or unmount. Events are deduped by `sequence`, so the
// snapshot a fresh (cursorless) connection replays is idempotent.

import { useEffect, useMemo, useState } from "react";
import { AgentRunEventSchema, type AgentRunEvent } from "./contracts";

const API_BASE =
  process.env.NEXT_PUBLIC_ATLAS_API_URL ?? "http://localhost:8730/api/v1";

export type StreamStatus = "idle" | "connecting" | "live" | "reconnecting" | "closed";

export interface AgentRunStream {
  events: AgentRunEvent[];
  status: StreamStatus;
}

export function useAgentRunStream(runId: string | null): AgentRunStream {
  const [events, setEvents] = useState<AgentRunEvent[]>([]);
  const [status, setStatus] = useState<StreamStatus>("idle");
  const [trackedRunId, setTrackedRunId] = useState<string | null>(runId);

  // Reset the buffer when the run changes — adjusted during render (the documented
  // React pattern for deriving state from a changed prop), NOT in an effect, so the
  // old run's trace never flashes and we avoid a set-state-in-effect cascade.
  if (runId !== trackedRunId) {
    setTrackedRunId(runId);
    setEvents([]);
    setStatus(runId ? "connecting" : "idle");
  }

  useEffect(() => {
    if (!runId) return;

    // Sequences already folded in — dedup across the initial snapshot and any
    // browser-driven reconnect replay. Local to the effect so it resets per run.
    const seen = new Set<number>();
    const url = `${API_BASE}/agent/runs/${encodeURIComponent(runId)}/stream`;
    const source = new EventSource(url);
    let closedByUs = false;

    source.addEventListener("connected", () => setStatus("live"));

    source.addEventListener("agent_event", (msg) => {
      let json: unknown;
      try {
        json = JSON.parse((msg as MessageEvent).data);
      } catch {
        return; // a malformed frame is skipped, not fatal
      }
      const parsed = AgentRunEventSchema.safeParse(json);
      if (!parsed.success || seen.has(parsed.data.sequence)) return;
      seen.add(parsed.data.sequence);
      setStatus("live");
      setEvents((prev) => {
        const next = [...prev, parsed.data];
        next.sort((a, b) => a.sequence - b.sequence);
        return next;
      });
    });

    source.addEventListener("heartbeat", () => setStatus("live"));

    source.addEventListener("stream_closed", () => {
      closedByUs = true;
      source.close();
      setStatus("closed");
    });

    source.onerror = () => {
      // Terminal close already handled above; otherwise the browser is retrying
      // (it will resend Last-Event-ID). Reflect that without tearing the source down.
      if (!closedByUs) setStatus("reconnecting");
    };

    return () => {
      closedByUs = true;
      source.close();
    };
  }, [runId]);

  return useMemo(() => ({ events, status }), [events, status]);
}
