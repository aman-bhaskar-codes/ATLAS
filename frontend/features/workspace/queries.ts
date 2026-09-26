import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { requestContract, AtlasApiError } from '@/lib/api/client';
import {
  WorkspaceListSchema,
  WorkspaceSchema,
  TreeResponseSchema,
  GitStatusSchema,
  DocumentSchema,
  GitDiffSchema
} from './contracts';

/** 503 means the subsystem is config-disabled — retrying cannot help. */
function shouldRetry(failureCount: number, error: unknown): boolean {
  if (error instanceof AtlasApiError && error.status === 503) return false;
  return failureCount < 2;
}

export function useWorkspaces() {
  return useQuery({
    queryKey: ['ide', 'workspaces'],
    queryFn: () => requestContract('/ide/workspaces', WorkspaceListSchema),
    refetchInterval: 10000,
    retry: shouldRetry,
  });
}

/**
 * A single workspace, resolved from the durable list. Shares the ['ide','workspaces']
 * cache with {@link useWorkspaces} (same queryKey), so the workbench reads the same
 * backend truth the Command Center does — no separate per-workspace endpoint, no
 * duplicated frontend state. Returns `null` when the id is not among the durable
 * workspaces (unknown id, or the IDE subsystem is disabled → 503 surfaces as error).
 */
export function useWorkspace(workspaceId: string | null) {
  return useQuery({
    queryKey: ['ide', 'workspaces'],
    queryFn: () => requestContract('/ide/workspaces', WorkspaceListSchema),
    enabled: !!workspaceId,
    retry: shouldRetry,
    select: (data) =>
      data.workspaces.find((w) => w.workspace_id === workspaceId) ?? null,
  });
}

export function useWorkspaceTree(workspaceId: string | null) {
  return useQuery({
    queryKey: ['ide', 'workspace', workspaceId, 'tree'],
    queryFn: () => requestContract(`/ide/workspaces/${workspaceId}/tree`, TreeResponseSchema),
    enabled: !!workspaceId,
  });
}

export function useWorkspaceGitStatus(workspaceId: string | null) {
  return useQuery({
    queryKey: ['ide', 'workspace', workspaceId, 'git', 'status'],
    queryFn: () => requestContract(`/ide/workspaces/${workspaceId}/git/status`, GitStatusSchema),
    enabled: !!workspaceId,
    refetchInterval: 10000,
  });
}

/**
 * The content + snapshot of a single workspace-relative file, from
 * GET /ide/workspaces/{id}/document?path=. Backend is source of truth: `version`
 * is the on-disk snapshot version the editor must echo back on save
 * (expected_version, Slice 3). Only fetched once a file is actually opened.
 */
export function useDocument(workspaceId: string | null, path: string | null) {
  return useQuery({
    queryKey: ['ide', 'workspace', workspaceId, 'document', path],
    queryFn: () =>
      requestContract(
        `/ide/workspaces/${workspaceId}/document?path=${encodeURIComponent(path ?? '')}`,
        DocumentSchema,
      ),
    enabled: !!workspaceId && !!path,
    retry: shouldRetry,
    staleTime: 5000,
  });
}

/**
 * The working-tree diff (or the staged diff when `staged` is true) from
 * GET /ide/workspaces/{id}/git/diff — read-only, funnel-routed. Refetched on the
 * same cadence as git status and invalidated by a successful save, so the diff
 * always reflects current on-disk truth.
 */
export function useGitDiff(workspaceId: string | null, staged = false) {
  return useQuery({
    queryKey: ['ide', 'workspace', workspaceId, 'git', 'diff', staged],
    queryFn: () =>
      requestContract(
        `/ide/workspaces/${workspaceId}/git/diff?staged=${staged ? 'true' : 'false'}`,
        GitDiffSchema,
      ),
    enabled: !!workspaceId,
  });
}

export function useOpenWorkspace() {
  const queryClient = useQueryClient();
  
  return useMutation({
    mutationFn: (data: { root_path: string; name: string }) => {
      return requestContract(
        '/ide/workspaces',
        WorkspaceSchema,
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(data)
        }
      );
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['ide', 'workspaces'] });
    }
  });
}
