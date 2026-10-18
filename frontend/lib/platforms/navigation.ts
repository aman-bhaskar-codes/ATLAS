import { agentApi } from '@/lib/api/client';

export type Platform = 'command' | 'research' | 'ide';

export interface LaunchPlatformResponse {
  task_id: string;
  workspace_id: string;
  handoff_token: string;
  redirect_path: string;
  platform: string;
}

export interface LaunchPlatformOptions {
  parentTaskId?: string;
  workspaceName?: string;
  initialQuery?: string;
}

export async function launchPlatform(
  target: 'research' | 'ide',
  opts?: LaunchPlatformOptions,
): Promise<LaunchPlatformResponse> {
  return await agentApi.launchPlatform({
    parent_task_id: opts?.parentTaskId ?? null,
    target,
    workspace_name: opts?.workspaceName ?? null,
    initial_query: opts?.initialQuery ?? null,
  });
}

export function platformForPath(path: string): Platform {
  if (path === '/research' || path.startsWith('/research/')) return 'research';
  if (['/workspaces', '/ide', '/dev'].includes(path) || path.startsWith('/ide/') || path.startsWith('/dev/')) return 'ide';
  return 'command';
}
export const PLATFORM_PATHS = { research: '/research', ide: '/workspaces' } as const;
/** Keep a handle to detect blockers, then detach the same-origin tab's opener. */
export function openPlatformWindow(
  platform: 'research' | 'ide',
  open: (path: string, target: string) => Window | null = (path, target) => window.open(path, target),
): { opened: boolean } {
  try {
    const handle = open(PLATFORM_PATHS[platform], `atlas-${platform}`);
    if (!handle || handle.closed) return { opened: false };
    handle.opener = null;
    handle.focus();
    return { opened: true };
  } catch { return { opened: false }; }
}
