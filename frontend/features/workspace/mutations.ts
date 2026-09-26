import { useMutation, useQueryClient } from '@tanstack/react-query';
import { requestContract } from '@/lib/api/client';
import {
  ChangeResultSchema,
  GitOpResultSchema,
  type ApplyChangeRequest,
  type ChangeResult,
  type GitOpResult,
} from './contracts';

/**
 * Apply a governed structured change to one file (POST /ide/workspaces/{id}/change).
 * Every write re-enters the backend SafetyEngine funnel + WorkspaceWriter — there is
 * no second write path. The result is honest about outcome: `applied` false with
 * `stale` true means the on-disk version drifted from `expected_version` and the
 * write was refused (never clobbered). On a real apply we invalidate the document
 * and tree/git queries so the UI re-reads backend truth (new version, new diff).
 */
export function useApplyChange(workspaceId: string) {
  const queryClient = useQueryClient();

  return useMutation<ChangeResult, Error, ApplyChangeRequest>({
    mutationFn: (body) =>
      requestContract(`/ide/workspaces/${workspaceId}/change`, ChangeResultSchema, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      }),
    onSuccess: (result) => {
      if (!result.applied) return; // stale / error: leave the draft intact for the user
      queryClient.invalidateQueries({
        queryKey: ['ide', 'workspace', workspaceId, 'document', result.path],
      });
      queryClient.invalidateQueries({
        queryKey: ['ide', 'workspace', workspaceId, 'tree'],
      });
      queryClient.invalidateQueries({
        queryKey: ['ide', 'workspace', workspaceId, 'git'],
      });
    },
  });
}

/**
 * Build the operation list for a full-file overwrite. A single `replace` spanning
 * `[0, line_count)` swaps the whole body; the backend recomputes the version hash.
 * `line_count` from the document snapshot equals the backend's `splitlines` count,
 * so the half-open range covers every existing line (and is a no-op insert at 0 for
 * an empty file).
 */
export function fullFileReplaceOps(newContent: string, lineCount: number) {
  return [
    {
      kind: 'replace' as const,
      start_line: 0,
      end_line: lineCount,
      text: newContent,
    },
  ];
}

/** Invalidate the git status + diff + tree queries so the SCM view re-reads backend
 * truth after a governed git write. Kept in one place so every git mutation refreshes
 * exactly the same surface. */
function invalidateGitSurface(queryClient: ReturnType<typeof useQueryClient>, workspaceId: string) {
  queryClient.invalidateQueries({ queryKey: ['ide', 'workspace', workspaceId, 'git'] });
  queryClient.invalidateQueries({ queryKey: ['ide', 'workspace', workspaceId, 'tree'] });
}

/**
 * Stage paths (or all changes) through the governed funnel
 * (POST /ide/workspaces/{id}/git/stage). On any non-denied response we refresh the
 * git surface — even a git failure changes nothing but the UI should re-read truth.
 */
export function useGitStage(workspaceId: string) {
  const queryClient = useQueryClient();
  return useMutation<GitOpResult, Error, { paths?: string[]; all_changes?: boolean }>({
    mutationFn: (body) =>
      requestContract(`/ide/workspaces/${workspaceId}/git/stage`, GitOpResultSchema, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ paths: body.paths ?? [], all_changes: body.all_changes ?? false }),
      }),
    onSuccess: () => invalidateGitSurface(queryClient, workspaceId),
  });
}

/** Unstage paths (or all) — index only (POST /ide/workspaces/{id}/git/unstage). */
export function useGitUnstage(workspaceId: string) {
  const queryClient = useQueryClient();
  return useMutation<GitOpResult, Error, { paths?: string[]; all_changes?: boolean }>({
    mutationFn: (body) =>
      requestContract(`/ide/workspaces/${workspaceId}/git/unstage`, GitOpResultSchema, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ paths: body.paths ?? [], all_changes: body.all_changes ?? false }),
      }),
    onSuccess: () => invalidateGitSurface(queryClient, workspaceId),
  });
}

/**
 * Commit the staged index (POST /ide/workspaces/{id}/git/commit). Committing with
 * nothing staged is git's honest non-zero exit surfaced as `ok:false` with git's
 * message — the caller shows `error`/`denied`, never a fabricated success.
 */
export function useGitCommit(workspaceId: string) {
  const queryClient = useQueryClient();
  return useMutation<GitOpResult, Error, { message: string }>({
    mutationFn: (body) =>
      requestContract(`/ide/workspaces/${workspaceId}/git/commit`, GitOpResultSchema, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      }),
    onSuccess: () => invalidateGitSurface(queryClient, workspaceId),
  });
}

/**
 * Create-and-switch to a new branch, or switch to an existing one when
 * `checkout_existing` is true (POST /ide/workspaces/{id}/git/branch).
 */
export function useGitBranch(workspaceId: string) {
  const queryClient = useQueryClient();
  return useMutation<GitOpResult, Error, { name: string; checkout_existing?: boolean }>({
    mutationFn: (body) =>
      requestContract(`/ide/workspaces/${workspaceId}/git/branch`, GitOpResultSchema, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: body.name, checkout_existing: body.checkout_existing ?? false }),
      }),
    onSuccess: () => invalidateGitSurface(queryClient, workspaceId),
  });
}
