import type { AuditExport } from './contracts';

export type AuditDateFilter = 'all' | 'today' | 'week' | 'month';
export interface AuditFilters {
  taskId?: string;
  correlationId?: string;
  executionId?: string;
  since?: string;
  tier?: number;
}

/** Today is UTC; other windows are rolling instants, shared by list and export. */
export function auditDateSince(filter: AuditDateFilter, now = new Date()): string | undefined {
  if (filter === 'all') return undefined;
  if (filter === 'today') return new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate())).toISOString();
  return new Date(now.getTime() - (filter === 'week' ? 7 : 30) * 86_400_000).toISOString();
}

export function auditQueryParams(filters: AuditFilters = {}, cursor?: string, limit?: number): URLSearchParams {
  const params = new URLSearchParams();
  if (cursor) params.set('cursor', cursor);
  if (limit !== undefined) params.set('limit', String(limit));
  if (filters.taskId) params.set('task_id', filters.taskId);
  if (filters.correlationId) params.set('correlation_id', filters.correlationId);
  if (filters.executionId) params.set('execution_id', filters.executionId);
  if (filters.since) params.set('since', filters.since);
  if (filters.tier !== undefined) params.set('tier', String(filters.tier));
  return params;
}

export function serializeAuditExport(data: AuditExport, format: 'json' | 'text'): string {
  if (format === 'json') return JSON.stringify(data, null, 2);
  const lines = [
    'ATLAS redacted audit export — newest recorded events first',
    `Generated (UTC): ${data.generated_at}`,
    `Since (inclusive): ${data.filters.since ?? 'any date'}`,
    `Tier: ${data.filters.tier ?? 'all'}`,
    `Maximum events: ${data.max_events}`,
    `Exported events: ${data.items.length}`,
    `Truncated: ${data.truncated ? 'yes — additional matching events were omitted' : 'no'}`,
    'Raw audit payloads are omitted.',
    '',
  ];
  // One JSON record per line prevents tabs/newlines in event values from
  // masquerading as another record; retains all public projection fields.
  return lines.concat(data.items.map(event => JSON.stringify(event))).join('\n');
}
