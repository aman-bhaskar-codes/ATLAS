import { z } from 'zod';

export const WorkspaceSchema = z.object({
  workspace_id: z.string(),
  session_id: z.string(),
  name: z.string(),
  root_paths: z.array(z.string()),
});
export type Workspace = z.infer<typeof WorkspaceSchema>;

export const WorkspaceListSchema = z.object({
  workspaces: z.array(WorkspaceSchema),
});

export const FileNodeSchema = z.object({
  path: z.string(),
  name: z.string(),
  is_dir: z.boolean(),
  size: z.number().nullable().optional(),
  version: z.string().nullable().optional(),
  language: z.string().nullable().optional(),
});
export type FileNode = z.infer<typeof FileNodeSchema>;

export const TreeResponseSchema = z.object({
  workspace_id: z.string(),
  nodes: z.array(FileNodeSchema),
});

export const GitFileChangeSchema = z.object({
  path: z.string(),
  state: z.string(),
  staged: z.boolean(),
  old_path: z.string().nullable().optional(),
});
export type GitFileChange = z.infer<typeof GitFileChangeSchema>;

export const GitStatusSchema = z.object({
  is_git_repo: z.boolean(),
  branch: z.string().default(""),
  ahead: z.number().default(0),
  behind: z.number().default(0),
  detached: z.boolean().default(false),
  has_conflicts: z.boolean().default(false),
  changes: z.array(GitFileChangeSchema).default([]),
});
export type GitStatus = z.infer<typeof GitStatusSchema>;

/** A document snapshot + its content, from GET /ide/workspaces/{id}/document?path= */
export const DocumentSchema = z.object({
  id: z.string(),
  path: z.string(),
  language: z.string(),
  version: z.string(),
  status: z.string(),
  line_count: z.number(),
  content: z.string(),
});
export type IdeDocument = z.infer<typeof DocumentSchema>;

/**
 * One structured edit operation, mirroring the backend `EditOperationRequest`
 * (capabilities/ide/contracts.py). Line/col are 0-based; ranges are half-open
 * `[start_line, end_line)`. A full-file save is a single `replace` spanning the
 * whole document.
 */
export const EditOperationSchema = z.object({
  kind: z.enum(['create', 'insert', 'replace', 'delete', 'rename', 'move']),
  start_line: z.number().nullable().optional(),
  start_col: z.number().nullable().optional(),
  end_line: z.number().nullable().optional(),
  end_col: z.number().nullable().optional(),
  text: z.string().nullable().optional(),
  new_path: z.string().nullable().optional(),
});
export type EditOperation = z.infer<typeof EditOperationSchema>;

/** Request body for POST /ide/workspaces/{id}/change. */
export const ApplyChangeRequestSchema = z.object({
  path: z.string(),
  /** null == expect the file to be absent (a create). */
  expected_version: z.string().nullable(),
  operations: z.array(EditOperationSchema),
  rationale: z.string().default(''),
});
export type ApplyChangeRequest = z.infer<typeof ApplyChangeRequestSchema>;

/**
 * Outcome of a governed write. `stale` means the on-disk version no longer
 * matched `expected_version` — the write was refused, never silently clobbered.
 */
export const ChangeResultSchema = z.object({
  path: z.string(),
  applied: z.boolean(),
  stale: z.boolean(),
  new_version: z.string().nullable().optional(),
  error: z.string().nullable().optional(),
});
export type ChangeResult = z.infer<typeof ChangeResultSchema>;

/** Per-file stat line in a git diff, from GET /ide/workspaces/{id}/git/diff. */
export const DiffStatSchema = z.object({
  path: z.string(),
  added: z.number().default(0),
  removed: z.number().default(0),
  binary: z.boolean().default(false),
  old_path: z.string().nullable().optional(),
});
export type DiffStat = z.infer<typeof DiffStatSchema>;

/**
 * Working-tree (or staged) diff. A non-git root comes back `is_git_repo:false`;
 * a clean tree is an honest empty diff (files:[], patch:""), never a 404.
 */
export const GitDiffSchema = z.object({
  is_git_repo: z.boolean(),
  staged: z.boolean().default(false),
  files: z.array(DiffStatSchema).default([]),
  patch: z.string().default(""),
});
export type GitDiff = z.infer<typeof GitDiffSchema>;

/**
 * Outcome of one governed git WRITE (stage/unstage/commit/branch). Honest like the
 * backend `GitOpResult`: `ok` reflects the real git exit, `denied` means the funnel
 * refused (nothing ran), `error` carries git's own message (e.g. "nothing to
 * commit"). `commit` is the new short SHA after a commit; `branch` the resulting
 * branch. Never a fabricated success.
 */
export const GitOpResultSchema = z.object({
  action: z.string(),
  ok: z.boolean().default(false),
  detail: z.string().default(""),
  commit: z.string().nullable().optional(),
  branch: z.string().nullable().optional(),
  denied: z.boolean().default(false),
  error: z.string().nullable().optional(),
});
export type GitOpResult = z.infer<typeof GitOpResultSchema>;

