// frontend/features/ide-checks/mutations.ts
//
// Run a test command, or a lint/type-check command, in the workspace and get back
// a STRUCTURED report. Both re-enter the existing IDEService → CommandRunner →
// SafetyEngine.guard funnel on the backend — there is no second execution path;
// these are the parsing counterparts of the one-shot `run_command`.
//
// A 503 means command execution is not wired on this runtime (no shell tool); the
// caller surfaces that honestly rather than pretending a run happened. Reuses the
// shared `isCommandExecutionDisabled` from the terminal feature.

import { useMutation } from "@tanstack/react-query";
import { requestContract } from "@/lib/api/client";
import { isCommandExecutionDisabled } from "@/features/ide-terminal/mutations";
import {
  TestReportSchema,
  DiagnosticReportSchema,
  type TestReport,
  type DiagnosticReport,
} from "./contracts";

export { isCommandExecutionDisabled };

/**
 * Run a test command in the workspace root and parse pass/fail. The default
 * timeout is generous (5 min) because a real suite is slow; the backend still
 * bounds it through the shell tool.
 */
export function useRunTests(workspaceId: string) {
  return useMutation<TestReport, Error, { command: string; timeoutS?: number }>({
    mutationFn: ({ command, timeoutS }) =>
      requestContract(`/ide/workspaces/${workspaceId}/tests`, TestReportSchema, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ command, ...(timeoutS ? { timeout_s: timeoutS } : {}) }),
      }),
  });
}

/**
 * Run a lint/type-check command in the workspace root and normalize its output
 * into structured problems. A linter exiting non-zero WITH findings is a normal
 * result (findings, not an error), so this mutation resolves — it does not throw.
 */
export function useCollectDiagnostics(workspaceId: string) {
  return useMutation<DiagnosticReport, Error, { command: string; timeoutS?: number }>({
    mutationFn: ({ command, timeoutS }) =>
      requestContract(`/ide/workspaces/${workspaceId}/diagnostics`, DiagnosticReportSchema, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ command, ...(timeoutS ? { timeout_s: timeoutS } : {}) }),
      }),
  });
}
