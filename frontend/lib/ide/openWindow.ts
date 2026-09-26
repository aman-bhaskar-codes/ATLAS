// frontend/lib/ide/openWindow.ts
//
// Launch the Development Workbench for a workspace in a SEPARATE browser window,
// per spec §1/§10. `window.open` can be blocked by a popup blocker; when it is,
// the returned handle is null (or immediately `closed`), and the caller must fall
// back to same-window navigation rather than silently doing nothing.

export const DEV_WINDOW_FEATURES =
  "noopener=false,width=1600,height=1000,menubar=no,toolbar=no,location=yes";

/** The route a workspace's workbench lives at. Single source of the path shape. */
export function devWorkspacePath(workspaceId: string): string {
  return `/dev/${encodeURIComponent(workspaceId)}`;
}

export type OpenWorkbenchResult =
  | { opened: true; window: Window }
  | { opened: false; reason: "blocked" };

/**
 * Open the workbench in a new window. Returns `{opened:false, reason:"blocked"}`
 * when the browser blocked the popup so the caller can offer "Open in current
 * window". Naming the new window by workspace id means a second launch for the
 * same workspace focuses the existing window instead of stacking duplicates.
 */
export function openWorkbenchWindow(workspaceId: string): OpenWorkbenchResult {
  const handle = window.open(
    devWorkspacePath(workspaceId),
    `atlas-dev-${workspaceId}`,
    DEV_WINDOW_FEATURES,
  );
  // A blocked popup is either null or an already-closed window.
  if (!handle || handle.closed) return { opened: false, reason: "blocked" };
  handle.focus();
  return { opened: true, window: handle };
}
