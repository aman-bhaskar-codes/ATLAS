"use client";

import React from 'react';
import { usePendingApprovals } from '../../features/trust/queries';
import { ApprovalCard } from '../../components/trust/ApprovalCard';

export default function ApprovalsPage() {
  const { data: approvals, isLoading, error } = usePendingApprovals();

  return (
    <>
      <div className="crumb mb-6">
        ATLAS / <strong>Approvals</strong>
      </div>

      <section className="panel">
        <div className="section-head">
          <h2>Pending Approvals</h2>
        </div>
        
        {isLoading ? (
          <div className="text-[var(--paper-500)] text-sm py-4">Loading approvals...</div>
        ) : error ? (
          <div className="text-[var(--danger-400)] text-sm py-4">Error loading approvals.</div>
        ) : approvals && approvals.length > 0 ? (
          <div className="flex flex-col">
            {approvals.map((approval: import('../../features/trust/contracts').ApprovalView) => (
              <ApprovalCard key={approval.id} approval={approval} />
            ))}
          </div>
        ) : (
          <div className="text-[var(--paper-500)] text-sm py-4">No pending approvals. ATLAS is operating autonomously.</div>
        )}
      </section>
    </>
  );
}
