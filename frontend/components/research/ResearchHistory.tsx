"use client";
import Link from 'next/link';
import { useResearchSessions } from '@/features/research/sessions';
import { researchSessionPath } from '@/lib/research/openWindow';
import { ErrorRow } from '@/components/primitives/ErrorState';
import { EmptyState } from '@/components/primitives/EmptyState';

export function ResearchHistory() {
  const history = useResearchSessions();
  return <section className="panel" aria-label="Research history">
    <div className="section-head"><h2>Recent sessions</h2></div>
    <div className="research-history">
      {history.isLoading && <p role="status">Loading research sessions…</p>}
      {history.isError && <ErrorRow error={history.error} onRetry={() => void history.refetch()} />}
      {history.data && history.data.sessions.length === 0 && <EmptyState title="No research sessions yet" description="Ask a question from Overview to begin." />}
      {history.data?.sessions.map(row => <Link className="research-history-row" key={row.session_id} href={researchSessionPath(row.session_id)}>
        <span>{row.question || 'Untitled question'}</span><span>{row.status} · {row.source_count} sources{row.parent_session_id ? ' · follow-up' : ''}</span>
      </Link>)}
    </div>
  </section>;
}
