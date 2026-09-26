// frontend/features/ide-terminal/mutations.ts
//
// Open a terminal session and start a command in it. Both re-enter the existing
// IDEService → TerminalSessionManager → SafetyEngine.guard funnel — there is no
// second execution path; the terminal is the streaming counterpart of the
// one-shot `run_command`, not a new capability. Output arrives over the SSE
// stream (see useTerminalStream), so these mutations only return the session id
// and whether the command actually started.

import { useMutation } from "@tanstack/react-query";
import { requestContract, AtlasApiError } from "@/lib/api/client";
import {
  OpenTerminalResponseSchema,
  TerminalRunResponseSchema,
  type OpenTerminalResponse,
  type TerminalRunResponse,
} from "./contracts";

/**
 * Open a streaming terminal rooted at the workspace. A 503 means command
 * execution is not wired on the backend (no shell tool) — the caller surfaces
 * that honestly rather than pretending a terminal exists.
 */
export function useOpenTerminal(workspaceId: string) {
  return useMutation<OpenTerminalResponse, Error, void>({
    mutationFn: () =>
      requestContract(`/ide/workspaces/${workspaceId}/terminal`, OpenTerminalResponseSchema, {
        method: "POST",
      }),
  });
}

/**
 * Start `command` in an open session as a background task; its output streams
 * over the session's SSE endpoint. `started === false` when the session is
 * unknown or already running a command (the backend refuses to interleave).
 */
export function useRunTerminalCommand(workspaceId: string) {
  return useMutation<TerminalRunResponse, Error, { terminalId: string; command: string }>({
    mutationFn: ({ terminalId, command }) =>
      requestContract(
        `/ide/workspaces/${workspaceId}/terminal/${encodeURIComponent(terminalId)}/run`,
        TerminalRunResponseSchema,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ command }),
        },
      ),
  });
}

/** True when an error is the backend's "command execution not available" 503. */
export function isCommandExecutionDisabled(error: unknown): boolean {
  return error instanceof AtlasApiError && error.status === 503;
}
