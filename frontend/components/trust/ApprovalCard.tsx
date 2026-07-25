import React, { useState } from 'react';
import { ApprovalView } from '../../features/trust/contracts';
import { DecisionBadge } from './DecisionBadge';
import { ExactPreview } from './ExactPreview';
import { useDecideApproval } from '../../features/trust/mutations';
import { generateIdempotencyKey, generateRequestId } from '../../features/trust/idempotency';

export function ApprovalCard({ approval }: { approval: ApprovalView }) {
  const { mutate: decide, isPending } = useDecideApproval();
  const [showPreview, setShowPreview] = useState(false);

  const handleDecision = (decision: 'approve' | 'deny') => {
    decide({
      approvalId: approval.id,
      decision,
      idempotencyKey: generateIdempotencyKey(),
      requestId: generateRequestId(),
    });
  };

  return (
    <div className="py-4 border-b border-[oklch(34%_0.035_278/0.65)] last:border-b-0">
      <div className="flex justify-between gap-2 items-start">
        <div className="text-[0.9rem] font-medium">{approval.operation}</div>
        <DecisionBadge tier={approval.tier} />
      </div>
      <div className="text-[0.78rem] text-[var(--paper-500)] my-1.5 mb-3">
        {approval.warnings.length > 0 ? approval.warnings.join(' · ') : 'No warnings'}
      </div>
      
      {showPreview && (
        <div className="mb-4">
          <ExactPreview 
            capability={approval.capability}
            operation={approval.operation}
            policy={approval.policy_version}
            bodyPreview={approval.exact_preview}
          />
        </div>
      )}

      <div className="flex gap-2">
        {!showPreview ? (
          <button 
            className="border border-[var(--line)] bg-[var(--ink-850)] text-[var(--paper-300)] rounded-md px-2.5 py-1.5 text-[0.75rem] transition-colors hover:border-[oklch(73%_0.13_162/0.38)] hover:text-[var(--jade-400)]"
            onClick={() => setShowPreview(true)}
          >
            Review preview
          </button>
        ) : (
          <button 
            className="border border-[oklch(73%_0.13_162/0.38)] bg-[var(--ink-850)] text-[var(--jade-400)] rounded-md px-2.5 py-1.5 text-[0.75rem] transition-colors hover:bg-[oklch(73%_0.13_162/0.1)] disabled:opacity-50"
            onClick={() => handleDecision('approve')}
            disabled={isPending}
          >
            Approve once
          </button>
        )}
        <button 
          className="border border-[var(--line)] bg-[var(--ink-850)] text-[var(--danger-400)] rounded-md px-2.5 py-1.5 text-[0.75rem] transition-colors hover:bg-[oklch(68%_0.18_22/0.1)] disabled:opacity-50"
          onClick={() => handleDecision('deny')}
          disabled={isPending}
        >
          Deny
        </button>
      </div>
    </div>
  );
}
