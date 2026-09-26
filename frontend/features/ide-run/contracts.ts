// frontend/features/ide-run/contracts.ts
//
// Zod contracts for the Run/Debug surface (Slice 7):
//   * managed dev processes — POST/GET/stop on
//     `/api/v1/ide/workspaces/{id}/processes[...]`
//   * the workspace ProjectModel — GET `/api/v1/ide/workspaces/{id}/project`,
//     the source of the run/test/build command CANDIDATES the panel offers.
//
// These mirror the backend source of truth exactly (routes_ide.py →
// DevProcessResponse / ProjectModelResponse) — no invented fields, no `any`. A
// managed process runs through the SAME SafetyEngine funnel as any shell dispatch
// (ProcessSupervisor → TerminalSessionManager → guard); the supervisor adds only
// lifecycle + port detection, never a second execution path.

import { z } from "zod";

/* ── ProjectModel (run candidates) ──────────────────────────────────────────── */

/**
 * The analyzed shape of a workspace. Only the command lists drive the Run panel,
 * but we validate the whole contract so a backend field rename fails loudly here
 * rather than silently dropping data. `run_commands` are CANDIDATES — nothing is
 * executed until the human launches one.
 */
export const ProjectModelSchema = z.object({
  root: z.string(),
  languages: z.array(z.string()).default([]),
  package_managers: z.array(z.string()).default([]),
  frameworks: z.array(z.string()).default([]),
  entrypoints: z.array(z.string()).default([]),
  test_commands: z.array(z.string()).default([]),
  build_commands: z.array(z.string()).default([]),
  run_commands: z.array(z.string()).default([]),
  dependencies: z.array(z.string()).default([]),
  file_count: z.number().int().default(0),
  indexed_symbols: z.number().int().default(0),
  fingerprint: z.string().default(""),
});
export type ProjectModel = z.infer<typeof ProjectModelSchema>;

/* ── Managed dev processes ──────────────────────────────────────────────────── */

/**
 * One managed dev process. `id` is ALSO its terminal id — output streams over the
 * existing terminal SSE endpoint (`/terminal/{id}/stream`), so the Run panel reuses
 * `useTerminalStream` verbatim. `detected_ports` are ports the server announced in
 * its own stdout (never guessed); `status` is the live lifecycle state.
 */
export const DevProcessSchema = z.object({
  id: z.string(),
  command: z.string(),
  cwd: z.string(),
  status: z.enum([
    "pending",
    "starting",
    "running",
    "healthy",
    "unhealthy",
    "exited",
    "failed",
    "killed",
  ]),
  detected_ports: z.array(z.number().int()).default([]),
  exit_code: z.number().int().nullable().default(null),
  started_ts: z.string().nullable().default(null),
  exited_ts: z.string().nullable().default(null),
});
export type DevProcess = z.infer<typeof DevProcessSchema>;

/** GET /processes — every managed process for the workspace (newest first). */
export const ProcessListSchema = z.object({
  processes: z.array(DevProcessSchema).default([]),
});
export type ProcessList = z.infer<typeof ProcessListSchema>;

/** POST /processes/{id}/stop — false when unknown or already finished. */
export const StopProcessResponseSchema = z.object({
  stopped: z.boolean(),
});
export type StopProcessResponse = z.infer<typeof StopProcessResponseSchema>;

/** A process whose lifecycle has ended — no live stream, no stop action. */
export function isProcessTerminal(status: DevProcess["status"]): boolean {
  return status === "exited" || status === "failed" || status === "killed";
}
