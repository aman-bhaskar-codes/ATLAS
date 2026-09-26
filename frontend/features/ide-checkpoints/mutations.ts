// frontend/features/ide-checkpoints/mutations.ts
//
// Create a checkpoint (snapshot the working tree) or restore one, in the workspace.
// Both re-enter the existing IDEService → GitCheckpoints → CommandRunner →
// SafetyEngine.guard funnel on the backend — there is no second execution path; a
// snapshot is a governed `git stash create` + `git update-ref`, a restore a governed
// `git checkout <sha> -- .`.
//
// On success we invalidate the checkpoint list (a snapshot adds one), and — for a
// restore, which rewrites on-disk files — the tree, open document, and git status so
// the workbench reflects real on-disk truth immediately. A 503 means command
// execution is not wired on this runtime; the caller surfaces that honestly rather
// than pretending a snapshot happened.

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { requestContract, AtlasApiError } from "@/lib/api/client";
import { CheckpointResultSchema, type CheckpointResult } from "./contracts";

/** Snapshot the current working tree under a new hidden checkpoint ref. */
export function useCreateCheckpoint(workspaceId: string) {
  const queryClient = useQueryClient();
  return useMutation<CheckpointResult, Error, { label?: string }>({
    mutationFn: ({ label }) =>
      requestContract(`/ide/workspaces/${workspaceId}/checkpoints`, CheckpointResultSchema, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ label: label ?? "" }),
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({
        queryKey: ["ide", "workspace", workspaceId, "checkpoints"],
      });
    },
  });
}

/**
 * Restore a checkpoint, replaying its captured tree over the workspace. Because this
 * rewrites files on disk, we invalidate everything the workbench reads from disk so
 * the editor/tree/git catch up to real bytes. An unknown checkpoint resolves as an
 * honest `ok:false` result (not a thrown error).
 */
export function useRestoreCheckpoint(workspaceId: string) {
  const queryClient = useQueryClient();
  return useMutation<CheckpointResult, Error, { checkpointId: string }>({
    mutationFn: ({ checkpointId }) =>
      requestContract(
        `/ide/workspaces/${workspaceId}/checkpoints/${encodeURIComponent(checkpointId)}/restore`,
        CheckpointResultSchema,
        { method: "POST" },
      ),
    onSuccess: (result) => {
      if (result.ok) {
        queryClient.invalidateQueries({ queryKey: ["ide", "workspace", workspaceId, "tree"] });
        queryClient.invalidateQueries({ queryKey: ["ide", "workspace", workspaceId, "document"] });
        queryClient.invalidateQueries({ queryKey: ["ide", "workspace", workspaceId, "git"] });
      }
    },
  });
}

/** True when an error is the backend's "command execution not available" 503. */
export function isCommandExecutionDisabled(error: unknown): boolean {
  return error instanceof AtlasApiError && error.status === 503;
}
