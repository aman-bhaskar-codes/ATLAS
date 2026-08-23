'use client';

import { unstable_catchError, type ErrorInfo } from 'next/error';

/**
 * A component-level error boundary for small pieces of chrome.
 *
 * WHY this is needed on top of `error.tsx` and `global-error.tsx`: the Topbar
 * lives in `app/layout.tsx`, and `error.tsx` explicitly does NOT wrap the layout
 * in its own segment. So without this, a throw while rendering the status pill —
 * a contract mismatch on `/runtime/status`, say — escalated straight to
 * `global-error.tsx` and replaced the entire application with an error page. A
 * status indicator failing is not a reason to take down the shell around it.
 *
 * `unstable_catchError` (Next 16.2+) is used rather than a hand-rolled React
 * error boundary because it leaves `redirect()` and `notFound()` alone (both work
 * by throwing), clears itself on client navigation, and gives `unstable_retry`
 * for free.
 *
 * The fallback deliberately shows no message and no retry button: it stands in for
 * a pill in a crowded header, so it has room for a label and nothing else. The
 * error still reaches the console for whoever is debugging.
 */
function ChromeFallback({ label }: { label: string }, { error }: ErrorInfo) {
  console.error(`[atlas] ${label} failed to render`, error);

  return (
    <span
      title={`${label} could not be displayed. The runtime itself is unaffected.`}
      aria-label={`${label} unavailable`}
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        gap: '0.45rem',
        padding: '0.3rem 0.6rem',
        borderRadius: '999px',
        border: '1px dashed var(--line)',
        fontSize: '0.7rem',
        letterSpacing: '0.03em',
        color: 'var(--paper-500)',
      }}
    >
      <span
        style={{
          display: 'inline-block',
          width: 8,
          height: 8,
          borderRadius: '50%',
          background: 'var(--paper-500)',
        }}
      />
      UNAVAILABLE
    </span>
  );
}

export const ChromeBoundary = unstable_catchError(ChromeFallback);
