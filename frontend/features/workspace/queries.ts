import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { requestContract, AtlasApiError } from '@/lib/api/client';
import {
  WorkspaceListSchema,
  WorkspaceSchema,
  TreeResponseSchema,
  GitStatusSchema
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
