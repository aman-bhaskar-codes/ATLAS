// frontend/features/ide-checkpoints/queries.ts
//
// Read-side hook for workspace checkpoints (Slice 10): the list of snapshots stored
// under the hidden `refs/atlas/checkpoints/*` namespace
// (GET /api/v1/ide/workspaces/{id}/checkpoints).
//
// Backend is the source of truth: nothing here fabricates a checkpoint. A 503 (no
// command tool wired on this runtime) is not retried — retrying cannot help.

import { useQuery } from "@tanstack/react-query";
import { requestContract, AtlasApiError } from "@/lib/api/client";
import { CheckpointListSchema } from "./contracts";

function shouldRetry(failureCount: number, error: unknown): boolean {
  if (error instanceof AtlasApiError && error.status === 503) return false;
  return failureCount < 2;
}

/** Stored checkpoints for the workspace, newest-committed first as the backend lists them. */
export function useCheckpoints(workspaceId: string | null) {
  return useQuery({
    queryKey: ["ide", "workspace", workspaceId, "checkpoints"],
    queryFn: () =>
      requestContract(`/ide/workspaces/${workspaceId}/checkpoints`, CheckpointListSchema),
    enabled: !!workspaceId,
    retry: shouldRetry,
    staleTime: 5_000,
  });
}
