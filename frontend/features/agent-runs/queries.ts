// frontend/features/agent-runs/queries.ts
//
// React Query hooks over the agent-run REST surface. The run list is scoped to the
// workbench's workspace_id so the Agent Panel only ever shows runs bound to THIS
// workspace — the same backend truth, no independent frontend state.

import { useQuery } from "@tanstack/react-query";
import { requestContract, AtlasApiError } from "@/lib/api/client";
import { RunListSchema, RunResponseSchema } from "./contracts";

/** 503 = agent engine config-disabled; 404 = unknown run — neither is retryable. */
function shouldRetry(failureCount: number, error: unknown): boolean {
  if (error instanceof AtlasApiError && (error.status === 503 || error.status === 404)) {
    return false;
  }
  return failureCount < 2;
}

/** Runs bound to one workspace, most-recently-updated first. */
export function useWorkspaceRuns(workspaceId: string | null, limit = 25) {
  return useQuery({
    queryKey: ["agent", "runs", { workspaceId, limit }],
    queryFn: () =>
      requestContract(
        `/agent/runs?workspace_id=${encodeURIComponent(workspaceId ?? "")}&limit=${limit}`,
        RunListSchema,
      ),
    enabled: !!workspaceId,
    retry: shouldRetry,
    refetchInterval: 10000,
  });
}

/**
 * A single run's full record + trace. The live trace arrives over SSE
 * ({@link useAgentRunStream}); this query backs the initial snapshot and the
 * terminal `result`. Polls while enabled so a backgrounded run's terminal result
 * lands even if the stream is not mounted.
 */
export function useRun(runId: string | null) {
  return useQuery({
    queryKey: ["agent", "run", runId],
    queryFn: () =>
      requestContract(`/agent/runs/${encodeURIComponent(runId ?? "")}`, RunResponseSchema),
    enabled: !!runId,
    retry: shouldRetry,
  });
}
