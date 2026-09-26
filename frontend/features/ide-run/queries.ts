// frontend/features/ide-run/queries.ts
//
// Read-side hooks for the Run/Debug panel:
//   * useProjectModel — the analyzed workspace, for the run/test/build command
//     candidates the panel offers (GET /ide/workspaces/{id}/project).
//   * useProcesses — the live list of managed dev processes, polled while any is
//     running so status + newly-detected ports surface without a manual refresh
//     (GET /ide/workspaces/{id}/processes).
//
// Backend is the source of truth: nothing here fabricates a process or a port.
// A 503 (IDE subsystem disabled) is not retried — retrying cannot help.

import { useQuery } from "@tanstack/react-query";
import { requestContract, AtlasApiError } from "@/lib/api/client";
import { ProjectModelSchema, ProcessListSchema } from "./contracts";
import { isProcessTerminal } from "./contracts";

function shouldRetry(failureCount: number, error: unknown): boolean {
  if (error instanceof AtlasApiError && error.status === 503) return false;
  return failureCount < 2;
}

/**
 * The workspace ProjectModel. The reported command lists are CANDIDATES the panel
 * surfaces; none run until launched. Stable per workspace, so a generous
 * staleTime keeps the panel from re-analyzing on every mount.
 */
export function useProjectModel(workspaceId: string | null) {
  return useQuery({
    queryKey: ["ide", "workspace", workspaceId, "project"],
    queryFn: () =>
      requestContract(`/ide/workspaces/${workspaceId}/project`, ProjectModelSchema),
    enabled: !!workspaceId,
    retry: shouldRetry,
    staleTime: 30_000,
  });
}

/**
 * Managed dev processes for the workspace. Self-paces its polling: it refetches
 * every 1.5s only while at least one process is still live (starting/running/
 * healthy), and stops once everything has reached a terminal state — so newly
 * announced ports and status transitions surface live without polling forever.
 */
export function useProcesses(workspaceId: string | null) {
  return useQuery({
    queryKey: ["ide", "workspace", workspaceId, "processes"],
    queryFn: () =>
      requestContract(`/ide/workspaces/${workspaceId}/processes`, ProcessListSchema),
    enabled: !!workspaceId,
    retry: shouldRetry,
    refetchInterval: (query) => {
      const data = query.state.data;
      const anyLive = !!data && data.processes.some((p) => !isProcessTerminal(p.status));
      return anyLive ? 1500 : false;
    },
  });
}
