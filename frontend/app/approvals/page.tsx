'use client';

import React from 'react';
import { TrustHeader } from '../../components/trust/TrustHeader';
import { usePendingApprovals } from '../../features/trust/queries';
import { ApprovalCard } from '../../components/trust/ApprovalCard';

export default function ApprovalsPage() {
  const { data: approvals, isLoading, error } = usePendingApprovals();

  return (
    <div className="max-w-5xl mx-auto py-8 px-6">
      <h1 className="text-3xl font-serif text-[var(--paper-100)] mb-6">Trust Center</h1>
      <TrustHeader active="approvals" />

      <div className="glass-panel p-6 rounded-lg">
        <h2 className="text-lg font-medium mb-4">Approval Inbox</h2>
        
        {isLoading ? (
          <div className="text-[var(--paper-500)] text-sm">Loading approvals...</div>
        ) : error ? (
          <div className="text-[var(--danger-400)] text-sm">Error loading approvals.</div>
        ) : approvals && approvals.length > 0 ? (
          <div className="flex flex-col">
            {approvals.map((approval: import('../../features/trust/contracts').ApprovalView) => (
              <ApprovalCard key={approval.id} approval={approval} />
            ))}
          </div>
        ) : (
          <div className="text-[var(--paper-500)] text-sm py-4">No pending approvals.</div>
        )}
      </div>
    </div>
  );
}
