// frontend/features/ide-run/mutations.ts
//
// Start and stop managed dev processes. Both re-enter the existing IDEService →
// ProcessSupervisor → TerminalSessionManager → SafetyEngine.guard funnel — there
// is no second execution path; a dev server is just a long-lived governed command
// whose output streams over the terminal SSE endpoint. On success we invalidate
// the process list so the panel reflects real backend state immediately.

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { requestContract, AtlasApiError } from "@/lib/api/client";
import {
  DevProcessSchema,
  StopProcessResponseSchema,
  type DevProcess,
  type StopProcessResponse,
} from "./contracts";

/**
 * Launch a long-lived dev process in the workspace root. A 503 means command
 * execution is not wired on this runtime (no shell tool); the caller surfaces that
 * honestly rather than pretending a process started.
 */
export function useStartProcess(workspaceId: string) {
  const queryClient = useQueryClient();
  return useMutation<DevProcess, Error, { command: string }>({
    mutationFn: ({ command }) =>
      requestContract(`/ide/workspaces/${workspaceId}/processes`, DevProcessSchema, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ command }),
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({
        queryKey: ["ide", "workspace", workspaceId, "processes"],
      });
    },
  });
}

/**
 * Stop a managed process. `stopped === false` when it is unknown to this workspace
 * or already finished — an honest expected outcome, never a raised error. The
 * terminal records a "stopped" exit; we invalidate the list to pick up the killed
 * status.
 */
export function useStopProcess(workspaceId: string) {
  const queryClient = useQueryClient();
  return useMutation<StopProcessResponse, Error, { processId: string }>({
    mutationFn: ({ processId }) =>
      requestContract(
        `/ide/workspaces/${workspaceId}/processes/${encodeURIComponent(processId)}/stop`,
        StopProcessResponseSchema,
        { method: "POST" },
      ),
    onSuccess: () => {
      queryClient.invalidateQueries({
        queryKey: ["ide", "workspace", workspaceId, "processes"],
      });
    },
  });
}

/** True when an error is the backend's "command execution not available" 503. */
export function isCommandExecutionDisabled(error: unknown): boolean {
  return error instanceof AtlasApiError && error.status === 503;
}
