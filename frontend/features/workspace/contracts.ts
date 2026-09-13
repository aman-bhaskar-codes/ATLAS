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
