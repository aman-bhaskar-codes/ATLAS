'use client'; // Error boundaries must be Client Components

/**
 * Root boundary — the last line of defence.
 *
 * This replaces `app/layout.tsx` when it fires, which is what makes it the only
 * thing that can catch a throw from the layout itself: the `Sidebar`, the
 * `Topbar` status pill, `Providers`, and the `CommandPalette` all render there,
 * and `app/error.tsx` does not wrap the layout in its own segment.
 *
 * Every colour below is a literal, and there is no import of `globals.css`, no
 * design token, and no other component. One of the ways this boundary gets
 * reached is a stylesheet or chunk that failed to load — so anything it depends
 * on to be readable is a dependency that may already be the thing that broke.
 * `metadata` exports are not supported in this file (it is a Client Component),
 * hence the React `<title>`.
 */
export default function GlobalError({
  error,
  unstable_retry,
}: {
  error: Error & { digest?: string };
  unstable_retry: () => void;
}) {
  return (
    // global-error must include html and body tags
    <html lang="en">
      <body
        style={{
          margin: 0,
          minHeight: '100vh',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          background: '#0b0b12',
          color: '#e8e8f0',
          fontFamily: 'ui-sans-serif, system-ui, -apple-system, sans-serif',
        }}
      >
        <title>ATLAS — interface error</title>
        <main style={{ maxWidth: '46ch', padding: '2rem' }}>
          <p
            style={{
              fontSize: '0.7rem',
              letterSpacing: '0.12em',
              textTransform: 'uppercase',
              color: '#f07a7a',
              margin: '0 0 0.75rem',
            }}
          >
            Interface error
          </p>
          <h1 style={{ fontSize: '1.4rem', fontWeight: 600, margin: '0 0 0.75rem' }}>
            The ATLAS interface failed to render
          </h1>
          <p style={{ fontSize: '0.9rem', lineHeight: 1.55, color: '#b4b4c6', margin: '0 0 0.5rem' }}>
            This is a failure in the web interface, not in the ATLAS runtime. Tasks that
            were already running are unaffected, and the API on port 8730 can still be
            reached directly.
          </p>
          {error.digest && (
            <p style={{ fontSize: '0.75rem', color: '#7c7c94', margin: '0 0 1.5rem' }}>
              Server reference: {error.digest}
            </p>
          )}
          <button
            onClick={() => unstable_retry()}
            style={{
              cursor: 'pointer',
              padding: '0.55rem 1.1rem',
              fontSize: '0.85rem',
              color: '#e8e8f0',
              background: 'transparent',
              border: '1px solid #3a3a4e',
              borderRadius: '8px',
            }}
          >
            Try again
          </button>
        </main>
      </body>
    </html>
  );
}
