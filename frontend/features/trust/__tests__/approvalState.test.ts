import { describe, expect, it } from 'vitest';
import { canApproveOnce } from '../approvalState';
import { ApprovalSchema } from '@/lib/api/contracts';
import { ApprovalViewSchema } from '../contracts';
const now = Date.parse('2026-10-08T12:00:00Z');
const ticket = { id: 'ticket', task_id: null, execution_id: null, correlation_id: 'cid', capability: 'fs', operation: 'delete', tier: 3, prompt: 'Approve once?', preview: 'path: /tmp/output', exact_preview: 'path: /tmp/output', warnings: [], policy_version: '1', created_at: '2026-10-08T12:00:00Z', expires_at: '2026-10-08T12:02:00Z', status: 'pending', decision_source: null, code_required: true, confirmation_code: '8765' };
describe('approval projection and one-time challenge', () => {
  it('accepts a unified ticket in both legacy and trust schemas', () => {
    expect(ApprovalSchema.parse(ticket).confirmation_code).toBe('8765');
    expect(ApprovalViewSchema.parse(ticket).exact_preview).toBe(ticket.preview);
  });
  it('requires reviewed preview and matching high-impact code', () => {
    expect(canApproveOnce(ticket, false, '8765', now)).toBe(false);
    expect(canApproveOnce(ticket, true, 'yes', now)).toBe(false);
    expect(canApproveOnce(ticket, true, '0000', now)).toBe(false);
    expect(canApproveOnce(ticket, true, '8765', now)).toBe(true);
  });
  it.each(['approved', 'denied', 'expired'])('does not authorize settled %s tickets', status => {
    expect(canApproveOnce({ ...ticket, status }, true, '8765', now)).toBe(false);
  });
  it('fails closed for expired, blocked and missing challenge tickets', () => {
    expect(canApproveOnce(ticket, true, '8765', now + 120000)).toBe(false);
    expect(canApproveOnce({ ...ticket, tier: 4 }, true, '8765', now)).toBe(false);
    expect(canApproveOnce({ ...ticket, confirmation_code: null }, true, '8765', now)).toBe(false);
  });
});
