// frontend/lib/research/openWindow.ts
//
// Launch the Perplexity-class Research surface for a session in a SEPARATE browser
// window, opened from the Command Center — mirrors lib/ide/openWindow.ts. A
// research session is a full-window surface (streaming answer + source rail +
// follow-ups) that owns its own bare chrome, so it opens outside the Command
// Center frame. `window.open` can be blocked by a popup blocker; when it is, the
// caller must fall back to same-window navigation rather than silently doing nothing.

export const RESEARCH_WINDOW_FEATURES =
  "noopener=false,width=1400,height=1000,menubar=no,toolbar=no,location=yes";

/** The route a research session lives at. Single source of the path shape. */
export function researchSessionPath(sessionId: string): string {
  return `/research/${encodeURIComponent(sessionId)}`;
}

export type OpenResearchResult =
  | { opened: true; window: Window }
  | { opened: false; reason: "blocked" };

/**
 * Open a research session in a new window. Returns `{opened:false,
 * reason:"blocked"}` when the browser blocked the popup so the caller can offer
 * "Open in current window". Naming the window by session id means a second launch
 * for the same session focuses the existing window instead of stacking duplicates.
 */
export function openResearchWindow(sessionId: string): OpenResearchResult {
  const handle = window.open(
    researchSessionPath(sessionId),
    `atlas-research-${sessionId}`,
    RESEARCH_WINDOW_FEATURES,
  );
  // A blocked popup is either null or an already-closed window.
  if (!handle || handle.closed) return { opened: false, reason: "blocked" };
  handle.focus();
  return { opened: true, window: handle };
}
