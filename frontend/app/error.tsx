'use client'; // Error boundaries must be Client Components

import { useEffect } from 'react';
import Link from 'next/link';
import { AlertTriangle } from 'lucide-react';
import { describeError } from '@/lib/api/errorMessage';

/**
 * Segment error boundary for everything rendered inside the app shell.
 *
 * WHY this file did not exist and needed to: there was no boundary of ANY kind in
 * the app. Any render-time throw — most realistically a query site reading
 * `data.something` while `data` was undefined after a failed fetch — unmounted the
 * whole tree and left a blank page with the error only in the console.
 *
 * `unstable_retry` (Next 16.2+, replaces the legacy `reset`) re-fetches and
 * re-renders this segment's children, so a backend that has since come back up
 * recovers without a full reload.
 *
 * NOT covered here: throws from `app/layout.tsx` itself, including the Topbar.
 * `error.tsx` does not wrap the layout in its own segment — that is what
 * `global-error.tsx` and the boundary around the status pill are for.
 */
export default function Error({
  error,
  unstable_retry,
}: {
  error: Error & { digest?: string };
  unstable_retry: () => void;
}) {
  useEffect(() => {
    // No error-reporting service by design (local-first, single-user). The console
    // is the honest destination; `digest` is what matches a server log line for a
    // Server Component throw, whose message is deliberately generic in production.
    console.error('[atlas] segment error', error);
  }, [error]);

  return (
    <div className="panel" style={{ margin: '1.5rem', padding: '2rem' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: '0.6rem', marginBottom: '0.75rem' }}>
        <AlertTriangle style={{ width: 22, height: 22, color: 'var(--danger-400)' }} />
        <h2 style={{ fontSize: '1.15rem', margin: 0, color: 'var(--paper-100)' }}>
          This view stopped rendering
        </h2>
      </div>
      <p style={{ color: 'var(--paper-300)', fontSize: '0.9rem', maxWidth: '52ch', marginBottom: '0.5rem' }}>
        {describeError(error)}
      </p>
      <p style={{ color: 'var(--paper-500)', fontSize: '0.78rem', maxWidth: '52ch', marginBottom: '1.5rem' }}>
        The rest of ATLAS is still running — the runtime is not affected by a UI error.
        {error.digest ? ` Server reference: ${error.digest}.` : ''}
      </p>
      <div style={{ display: 'flex', gap: '0.6rem' }}>
        <button className="primary" style={{ width: 'auto' }} onClick={() => unstable_retry()}>
          Try again
        </button>
        <Link href="/" className="ghost-btn" style={{ display: 'inline-flex', alignItems: 'center', width: 'auto' }}>
          Command Center
        </Link>
      </div>
    </div>
  );
}
