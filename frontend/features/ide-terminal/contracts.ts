// frontend/features/ide-terminal/contracts.ts
//
// Zod contracts for the interactive streaming terminal surface
// (`/api/v1/ide/workspaces/{id}/terminal*`). These mirror the backend source of
// truth exactly (routes_ide.py + capabilities/ide/terminal.py) — no invented
// fields, no `any`. Every command still runs through the SAME SafetyEngine funnel
// as any other shell dispatch; the terminal only changes delivery to incremental.
//
// The SSE stream frames two payload shapes (`event: chunk` / `event: exit`); the
// `connected` and `stream_closed` frames carry no state the reducer needs. We keep
// separate schemas so a malformed frame of one kind never corrupts the other.

import { z } from "zod";

/* ── REST responses ────────────────────────────────────────────────────────── */

/** POST /terminal — the id of the newly-allocated session (no process yet). */
export const OpenTerminalResponseSchema = z.object({
  terminal_id: z.string(),
});
export type OpenTerminalResponse = z.infer<typeof OpenTerminalResponseSchema>;

/** POST /terminal/{id}/run — false when the session is unknown or already busy. */
export const TerminalRunResponseSchema = z.object({
  started: z.boolean(),
});
export type TerminalRunResponse = z.infer<typeof TerminalRunResponseSchema>;

/* ── SSE frame payloads (terminal.py → TerminalEvent) ───────────────────────── */

/** One `event: chunk` frame's `data`: a line of live output on stdout/stderr. */
export const TerminalChunkSchema = z.object({
  stream: z.string(), // "stdout" | "stderr"
  data: z.string(),
});
export type TerminalChunk = z.infer<typeof TerminalChunkSchema>;

/** The terminal `event: exit` frame's `data`: how the command ended. */
export const TerminalExitSchema = z.object({
  exit_code: z.number().int().nullable().default(null),
  denied: z.boolean().default(false),
  error: z.string().nullable().default(null),
});
export type TerminalExit = z.infer<typeof TerminalExitSchema>;
