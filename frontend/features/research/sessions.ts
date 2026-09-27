// frontend/features/research/sessions.ts
//
// React Query hooks over the persisted research-SESSION REST surface
// (/api/v1/research). The SAME backend truth the SSE stream folds — this backs
// the initial snapshot, the session list, and the terminal grounded answer for a
// backgrounded run even when the stream is not mounted. No independent frontend
// state; every field has a real backend origin (§69).
//
// Distinct from the legacy poll-based `useResearchTask` in ./queries.ts (R9,
// /tasks in-chrome view) — these drive the new Perplexity session surface.

import { useQuery } from "@tanstack/react-query";
import { requestContract, AtlasApiError } from "@/lib/api/client";
import { ResearchSessionSchema, ResearchSessionListSchema } from "./contracts";

/** 503 = research subsystem config-disabled; 404 = unknown session — neither retryable. */
function shouldRetry(failureCount: number, error: unknown): boolean {
  if (error instanceof AtlasApiError && (error.status === 503 || error.status === 404)) {
    return false;
  }
  return failureCount < 2;
}

/** All research sessions, most-recently-updated first (compact rows). */
export function useResearchSessions(limit = 50) {
  return useQuery({
    queryKey: ["research", "sessions", { limit }],
    queryFn: () =>
      requestContract(`/research/sessions?limit=${limit}`, ResearchSessionListSchema),
    retry: shouldRetry,
    refetchInterval: 10000,
  });
}

/**
 * A single session's full record: grounded answer + source rail + trace summary.
 * The live phase trace arrives over SSE ({@link useResearchStream}); this query
 * backs the snapshot and the terminal answer. Polls while enabled so a
 * backgrounded run's terminal result lands even if the stream is not mounted.
 */
export function useResearchSession(sessionId: string | null) {
  return useQuery({
    queryKey: ["research", "session", sessionId],
    queryFn: () =>
      requestContract(
        `/research/sessions/${encodeURIComponent(sessionId ?? "")}`,
        ResearchSessionSchema,
      ),
    enabled: !!sessionId,
    retry: shouldRetry,
  });
}
