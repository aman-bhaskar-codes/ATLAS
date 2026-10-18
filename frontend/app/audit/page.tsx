"use client";

import { Suspense, useMemo } from 'react';
import { useMutation } from '@tanstack/react-query';
import { usePathname, useRouter, useSearchParams } from 'next/navigation';
import { exportAuditLog, useAuditLog } from '@/features/trust/queries';
import { auditDateSince, serializeAuditExport, type AuditDateFilter } from '@/features/trust/audit';
import { Button } from '@/components/primitives/Button';
import { ErrorState } from '@/components/primitives/ErrorState';
import { EmptyState } from '@/components/primitives/EmptyState';

const controlStyle = { padding: 'var(--space-sm) var(--space-md)', background: 'var(--surface-sunken)', border: '1px solid var(--border-subtle)', color: 'var(--text-secondary)' };
const cellStyle = { padding: 'var(--space-sm) var(--space-md)' };

function AuditLog() {
  const searchParams = useSearchParams();
  const router = useRouter();
  const pathname = usePathname();
  const dateValue = searchParams.get('date');
  const dateFilter: AuditDateFilter = dateValue === 'today' || dateValue === 'week' || dateValue === 'month' ? dateValue : 'all';
  const tierValue = searchParams.get('tier');
  const tierFilter = tierValue !== null && /^[0-4]$/.test(tierValue) ? tierValue : 'all';
  const cursor = searchParams.get('cursor') || undefined;
  const sinceValue = searchParams.get('since');
  const filters = useMemo(() => ({
    since: dateFilter === 'all' ? undefined : sinceValue || auditDateSince(dateFilter),
    tier: tierFilter === 'all' ? undefined : Number(tierFilter),
  }), [dateFilter, sinceValue, tierFilter]);
  const { data, isLoading, isFetching, isError, error, refetch } = useAuditLog(cursor, 50, filters);
  const events = data?.items ?? [];
  const exportMutation = useMutation({
    mutationFn: async (format: 'json' | 'text') => {
      const result = await exportAuditLog(filters);
      const blob = new Blob([serializeAuditExport(result, format)], {type: format === 'json' ? 'application/json' : 'text/plain;charset=utf-8'});
      const url = URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = url;
      link.download = `atlas-audit-redacted-${result.generated_at.slice(0, 10)}.${format === 'json' ? 'json' : 'txt'}`;
      document.body.appendChild(link);
      link.click();
      link.remove();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
      return result;
    },
  });
  const updateLocation = (params: URLSearchParams) => {
    exportMutation.reset();
    router.push(`${pathname}?${params}`, {scroll: false});
  };
  const updateFilter = (key: 'date' | 'tier', value: string) => {
    const params = new URLSearchParams(searchParams);
    params.delete('cursor');
    if (value === 'all') params.delete(key); else params.set(key, value);
    if (key === 'date') {
      const since = auditDateSince(value as AuditDateFilter);
      if (since) params.set('since', since); else params.delete('since');
    }
    updateLocation(params);
  };
  const navigatePage = (nextCursor?: string) => {
    const params = new URLSearchParams(searchParams);
    if (nextCursor) params.set('cursor', nextCursor); else params.delete('cursor');
    if (filters.since) params.set('since', filters.since);
    updateLocation(params);
  };

  return (
    <>
      <div className="crumb mb-6">ATLAS / <strong>Audit Log</strong></div>
      <div className="flex flex-wrap items-center justify-between gap-4 mb-4">
        <div className="flex flex-wrap gap-4">
          <label className="flex flex-col gap-1 text-sm text-fg-muted">Date
            <select aria-label="Audit date" className="ghost-btn" value={dateFilter} disabled={exportMutation.isPending} onChange={e => updateFilter('date', e.target.value)} style={controlStyle}>
              <option value="all">Any Date</option><option value="today">Today (UTC)</option>
              <option value="week">Last 7 days</option><option value="month">Last 30 days</option>
            </select>
          </label>
          <label className="flex flex-col gap-1 text-sm text-fg-muted">Tier
            <select aria-label="Audit tier" className="ghost-btn" value={tierFilter} disabled={exportMutation.isPending} onChange={e => updateFilter('tier', e.target.value)} style={controlStyle}>
              <option value="all">All Tiers</option><option value="0">Tier 0</option><option value="1">Tier 1</option>
              <option value="2">Tier 2</option><option value="3">Tier 3</option><option value="4">Tier 4</option>
            </select>
          </label>
        </div>
        <div className="flex flex-wrap gap-2" aria-busy={exportMutation.isPending}>
          <Button variant="secondary" size="sm" disabled={exportMutation.isPending || isLoading || isError} onClick={() => exportMutation.mutate('json')}>Export JSON</Button>
          <Button variant="secondary" size="sm" disabled={exportMutation.isPending || isLoading || isError} onClick={() => exportMutation.mutate('text')}>Export Text</Button>
        </div>
      </div>
      <p className="text-sm text-fg-muted mb-4">Export the newest matching events across the log, up to 1,000. Raw payloads are omitted; each file reports its filters and truncation.</p>
      <div aria-live="polite" className="mb-4" style={{minHeight: 'var(--control-sm)'}}>
        {exportMutation.isPending && <p role="status" className="text-sm text-fg-muted">Preparing redacted export…</p>}
        {exportMutation.isError && <ErrorState title="Audit export failed" error={exportMutation.error} />}
        {exportMutation.isSuccess && <p role="status" className="text-sm text-fg-muted">Downloaded {exportMutation.data.items.length} redacted events.{exportMutation.data.truncated ? ' Export truncated at 1,000 events; additional matches were omitted.' : ' All matching events included.'}</p>}
      </div>
      <section className="panel" aria-busy={isFetching}>
        <div className="section-head"><h2>System Audit Trail</h2></div>
        {isLoading ? <div role="status" className="text-fg-muted text-sm py-4 px-4">Loading audit log...</div>
          : isError ? <ErrorState title="Failed to load audit log" error={error} onRetry={() => void refetch()} />
          : events.length > 0 ? (
          <div style={{overflowX: 'auto'}}>
            <table style={{width: '100%', borderCollapse: 'collapse', fontSize: 'var(--text-md)', textAlign: 'left'}}>
              <thead><tr style={{borderBottom: '1px solid var(--border-subtle)', color: 'var(--text-muted)'}}>
                {['Timestamp (UTC)', 'Actor', 'Action', 'Tier', 'Capability', 'Outcome'].map(label => <th scope="col" key={label} style={{...cellStyle, fontWeight: 'normal'}}>{label}</th>)}
              </tr></thead>
              <tbody>{events.map(event => (
                <tr key={event.id} style={{borderBottom: '1px solid var(--border-subtle)'}}>
                  <td style={{...cellStyle, whiteSpace: 'nowrap', color: 'var(--text-muted)'}} className="mono">{new Date(event.ts).toLocaleString(undefined, {timeZone: 'UTC'})}</td>
                  <td style={cellStyle}>{event.actor}</td><td style={cellStyle}>{event.action}</td><td style={cellStyle}>{event.tier ?? 'Unknown'}</td>
                  <td style={{...cellStyle, color: 'var(--accent)'}}>{event.capability || '—'}</td>
                  <td style={cellStyle}><span className={event.outcome === 'success' ? 'text-ok' : event.outcome === 'denied' ? 'text-danger-400' : 'text-fg-secondary'}>{event.outcome || '—'}</span></td>
                </tr>
              ))}</tbody>
            </table>
          </div>
        ) : <EmptyState title="No audit events found" description={dateFilter !== 'all' || tierFilter !== 'all' ? 'No events match these filters.' : cursor ? 'No older events on this page. Return to the newest events.' : 'No recorded audit events are available.'} />}
        <div className="flex flex-wrap justify-between items-center gap-3 py-4" aria-label="Audit pagination">
          <p role="status" className="text-sm text-fg-muted">{isError ? 'Page unavailable' : isLoading ? 'Loading page…' : `${events.length} events on this page · ${data?.next_cursor ? 'More matching events available' : 'End of matching events'}`}</p>
          <div className="flex gap-2">
            <Button variant="ghost" size="sm" disabled={!cursor || isFetching || exportMutation.isPending} onClick={() => navigatePage()}>Newest events</Button>
            <Button variant="secondary" size="sm" disabled={!data?.next_cursor || isFetching || isError || exportMutation.isPending} onClick={() => navigatePage(data?.next_cursor ?? undefined)}>Older events</Button>
          </div>
        </div>
      </section>
    </>
  );
}

export default function AuditPage() {
  return <Suspense fallback={<div role="status" className="text-fg-muted text-sm py-4">Loading audit log...</div>}><AuditLog /></Suspense>;
}
