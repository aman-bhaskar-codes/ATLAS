"use client";
import React, { useEffect, useRef, useState } from 'react';
import { ApprovalView } from '../../features/trust/contracts';
import { DecisionBadge } from './DecisionBadge';
import { ExactPreview } from './ExactPreview';
import { useDecideApproval } from '../../features/trust/mutations';
import { generateIdempotencyKey, generateRequestId } from '../../features/trust/idempotency';
import { ErrorRow } from '@/components/primitives/ErrorState';
import { canApproveOnce } from "@/features/trust/approvalState";
import { Button } from '@/components/primitives/Button';

export function ApprovalCard({ approval }: { approval: ApprovalView }) {
  const mutation = useDecideApproval();
  const [showPreview, setShowPreview] = useState(false);
  const [code, setCode] = useState('');
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => { const timer = window.setInterval(() => setNow(Date.now()), 1000); return () => window.clearInterval(timer); }, []);
  const lastAttempt = useRef<{ signature: string; key: string; requestId: string } | null>(null);
  const settled = approval.status !== 'pending' || Date.parse(approval.expires_at) <= now || mutation.isSuccess;
  const needsCode = approval.code_required || approval.tier >= 3;
  const canApprove = canApproveOnce(approval, showPreview, code, now);
  const handleDecision = (decision: 'approve' | 'deny') => {
    if (mutation.isPending || settled || (decision === 'approve' && !canApprove)) return;
    const confirmationCode = decision === 'approve' && needsCode ? code.trim() : undefined;
    const signature = `${decision}:${confirmationCode ?? ''}`;
    if (lastAttempt.current?.signature !== signature) lastAttempt.current = { signature, key: generateIdempotencyKey(), requestId: generateRequestId() };
    mutation.mutate({ approvalId: approval.id, decision, idempotencyKey: lastAttempt.current.key, requestId: lastAttempt.current.requestId, confirmationCode });
  };
  return <article className="py-4 border-b border-[var(--line)]" aria-label={`Approval ${approval.operation}`}>
    <div className="flex justify-between gap-2 items-start"><h3 className="text-sm">{approval.capability}.{approval.operation}</h3><DecisionBadge tier={approval.tier} /></div>
    <p className="text-sm my-2">{approval.prompt}</p>
    <p className="text-xs text-[var(--paper-500)]">Expires {new Date(approval.expires_at).toLocaleString()}</p>
    {approval.warnings.length > 0 && <ul className="text-xs my-3">{approval.warnings.map((warning, index) => <li key={index}>{warning}</li>)}</ul>}
    {showPreview && <div className="my-4"><ExactPreview capability={approval.capability} operation={approval.operation} policy={approval.policy_version} bodyPreview={approval.exact_preview} /></div>}
    {showPreview && needsCode && approval.tier < 4 && <div className="my-4">
      <label htmlFor={`approval-code-${approval.id}`}>Confirmation code</label>
      <p className="text-xs">High-impact action. Type <strong>{approval.confirmation_code ?? 'the code from the approval challenge'}</strong> to approve once.</p>
      <input id={`approval-code-${approval.id}`} value={code} onChange={event => setCode(event.target.value)} inputMode="numeric" autoComplete="off" maxLength={4} disabled={mutation.isPending || settled} className="bg-[var(--ink-950)] border border-[var(--line)] rounded p-2 my-2" />
    </div>}
    {mutation.isError && <ErrorRow error={mutation.error} />}
    {mutation.isSuccess && <p role="status" className="text-sm">Decision recorded: {mutation.data.status}. This permits only this waiting action.</p>}
    <div className="flex flex-wrap gap-2 mt-3">
      {!showPreview ? <Button variant="secondary" onClick={() => setShowPreview(true)}>Review preview</Button> : <Button onClick={() => handleDecision('approve')} disabled={mutation.isPending || settled || !canApprove}>Approve once</Button>}
      <Button variant="secondary" onClick={() => handleDecision('deny')} disabled={mutation.isPending || settled}>Deny</Button>
    </div>
  </article>;
}
