// frontend/features/research/useResearchStream.ts
//
// Live SSE client for a research session's durable phase trace
// (GET /research/sessions/{id}/stream). The backend frames named events —
// `connected`, `research_event` (with `id: {sequence}`), `heartbeat`,
// `stream_closed` — and resumes from the `Last-Event-ID` header.
//
// The authenticated transport retains the replay cursor across transient failures.

import { AtlasEventStream } from "../../lib/api/stream";
import { useEffect, useMemo, useState } from "react";
import { ResearchEventSchema, type ResearchEvent } from "./contracts";

const API_BASE =
  process.env.NEXT_PUBLIC_ATLAS_API_URL ?? "http://localhost:8730/api/v1";

export type StreamStatus = "idle" | "connecting" | "live" | "reconnecting" | "closed";

export interface ResearchStream {
  events: ResearchEvent[];
  status: StreamStatus;
}

export function useResearchStream(sessionId: string | null): ResearchStream {
  const [events, setEvents] = useState<ResearchEvent[]>([]);
  const [status, setStatus] = useState<StreamStatus>("idle");
  const [trackedSessionId, setTrackedSessionId] = useState<string | null>(sessionId);

  // Reset the buffer when the session changes — adjusted during render (the
  // documented React pattern for deriving state from a changed prop), NOT in an
  // effect, so the old session's trace never flashes and we avoid a
  // set-state-in-effect cascade.
  if (sessionId !== trackedSessionId) {
    setTrackedSessionId(sessionId);
    setEvents([]);
    setStatus(sessionId ? "connecting" : "idle");
  }

  useEffect(() => {
    if (!sessionId) return;

    // Sequences already folded in — dedup across the initial snapshot and any
    // browser-driven reconnect replay. Local to the effect so it resets per session.
    const seen = new Set<number>();
    const url = `${API_BASE}/research/sessions/${encodeURIComponent(sessionId)}/stream`;
    const source = new AtlasEventStream(url);
    let closedByUs = false;

    source.addEventListener("connected", () => setStatus("live"));

    source.addEventListener("research_event", (msg) => {
      let json: unknown;
      try {
        json = JSON.parse((msg as MessageEvent).data);
      } catch {
        return; // a malformed frame is skipped, not fatal
      }
      const parsed = ResearchEventSchema.safeParse(json);
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

    source.onerror = ({ retrying }) => {
      // A credential error pauses attempts until the operator updates Settings.
      if (!closedByUs) setStatus(retrying ? "reconnecting" : "closed");
    };

    return () => {
      closedByUs = true;
      source.close();
    };
  }, [sessionId]);

  return useMemo(() => ({ events, status }), [events, status]);
}
