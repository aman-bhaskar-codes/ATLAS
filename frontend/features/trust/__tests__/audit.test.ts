import { describe, expect, it } from 'vitest';
import { auditDateSince, auditQueryParams, serializeAuditExport } from '../audit';
import { AuditExportSchema } from '../contracts';

describe('audit filters and safe exports', () => {
  it('uses UTC midnight for today and exact rolling windows for 7/30 days', () => {
    const now = new Date('2026-10-06T12:34:56Z');
    expect(auditDateSince('today', now)).toBe('2026-10-06T00:00:00.000Z');
    expect(auditDateSince('week', now)).toBe('2026-09-29T12:34:56.000Z');
    expect(auditDateSince('month', now)).toBe('2026-09-06T12:34:56.000Z');
    expect(auditDateSince('all', now)).toBeUndefined();
  });
  it('includes tier zero and filters with paginated queries', () => {
    const params = auditQueryParams({tier: 0, since: '2026-10-06T00:00:00Z', taskId: 'task/+ ?'}, '50', 50);
    expect(params.get('tier')).toBe('0');
    expect(params.get('since')).toBe('2026-10-06T00:00:00Z');
    expect(params.get('task_id')).toBe('task/+ ?');
    expect(params.get('cursor')).toBe('50');
  });
  it('exports only the validated projection with scope and truncation metadata', () => {
    const result = AuditExportSchema.parse({
      items: [{id: '1', ts: '2026-10-06T12:00:00Z', actor: 'user', action: 'read', tool: null,
        capability: null, tier: 0, decision: null, outcome: null, task_id: null, correlation_id: 'c',
        execution_id: null, redaction: 'partial', safe_payload_summary: null, payload: {password: 'secret'}}],
      generated_at: '2026-10-06T12:00:00Z', filters: {since: null, tier: 0}, max_events: 1000, truncated: true,
    });
    const json = serializeAuditExport(result, 'json');
    expect(json).not.toContain('password');
    expect(JSON.parse(json).truncated).toBe(true);
    const text = serializeAuditExport(result, 'text');
    expect(text).toContain('Truncated: yes');
    expect(text).toContain('Tier: 0');
    expect(text).toContain('Maximum events: 1000');
    expect(text).not.toContain('password');
  });
});
