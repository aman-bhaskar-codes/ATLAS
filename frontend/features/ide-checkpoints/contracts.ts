// frontend/features/ide-checkpoints/contracts.ts
//
// Zod contracts for workspace checkpoints (Slice 10):
//   * POST /api/v1/ide/workspaces/{id}/checkpoints              → CheckpointResult (snapshot)
//   * GET  /api/v1/ide/workspaces/{id}/checkpoints              → CheckpointList
//   * POST /api/v1/ide/workspaces/{id}/checkpoints/{cid}/restore → CheckpointResult (restore)
//
// These mirror the backend source of truth exactly (routes_ide.py →
// CheckpointResultResponse / CheckpointRefResponse / CheckpointListResponse). A
// snapshot pins the working tree under a hidden `refs/atlas/checkpoints/*` ref via
// `git stash create` + `git update-ref` (side-effect free); a restore replays it
// with `git checkout <sha> -- .`. Both re-enter the SAME SafetyEngine funnel as any
// governed git write — no second execution path; this layer only builds the command
// string and parses the governed output.
//
// HONESTY (mirrors the backend): `ok` reflects the real git process, `denied` means
// SafetyEngine refused (nothing ran), and a missing checkpoint restore is an honest
// `ok:false` with an `error`, never a fabricated success. The UI renders exactly
// what the backend reports.

import { z } from "zod";

/** The structured outcome of one snapshot or restore. */
export const CheckpointResultSchema = z.object({
  action: z.string(), // "snapshot" | "restore"
  ok: z.boolean().default(false),
  checkpoint_id: z.string().nullable().default(null),
  commit: z.string().nullable().default(null),
  label: z.string().default(""),
  clean: z.boolean().default(false),
  detail: z.string().default(""),
  denied: z.boolean().default(false),
  error: z.string().nullable().default(null),
});
export type CheckpointResult = z.infer<typeof CheckpointResultSchema>;

/** One stored checkpoint, from the hidden `refs/atlas/checkpoints/*` namespace. */
export const CheckpointRefSchema = z.object({
  checkpoint_id: z.string(),
  commit: z.string(),
  label: z.string().default(""),
});
export type CheckpointRef = z.infer<typeof CheckpointRefSchema>;

export const CheckpointListSchema = z.object({
  checkpoints: z.array(CheckpointRefSchema).default([]),
});
export type CheckpointList = z.infer<typeof CheckpointListSchema>;
