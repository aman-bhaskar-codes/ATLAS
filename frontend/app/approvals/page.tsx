"use client";

import React from 'react';
import { usePendingApprovals } from '../../features/trust/queries';
import { ApprovalCard } from '../../components/trust/ApprovalCard';
import { ErrorState } from '@/components/primitives/ErrorState';
import { EmptyState } from '@/components/primitives/EmptyState';

export default function ApprovalsPage() {
  const { data: approvals, isLoading, isError, error } = usePendingApprovals();

  return (
    <>
      <div className="crumb mb-6">
        ATLAS / <strong>Approvals</strong>
      </div>

      <div className="grid-cols-panel" style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '2rem' }}>
        <section className="panel">
          <div className="section-head">
            <h2>Pending Actions</h2>
          </div>
          
          {isLoading ? (
            <div className="text-[var(--paper-500)] text-sm py-4 px-4">Loading approvals...</div>
          ) : isError ? (
            <ErrorState title="Failed to load approvals" error={error} />
          ) : approvals && approvals.length > 0 ? (
            <div className="flex flex-col px-4">
              {approvals.map((approval: import('../../features/trust/contracts').ApprovalView) => (
                <ApprovalCard key={approval.id} approval={approval} />
              ))}
            </div>
          ) : (
            <EmptyState 
              title="Inbox Zero" 
              description="No pending actions. The ATLAS Safety Engine automatically intercepts Tier 2 (destructive) and Tier 3 (irreversible) actions." 
            />
          )}
        </section>

        <section className="panel">
          <div className="section-head">
            <h2>Recently Resolved</h2>
          </div>
          <div className="px-4 py-4 text-sm text-[var(--paper-300)]">
            <div className="py-3 border-b border-[var(--line)]">
              <div className="flex justify-between mb-1">
                <span className="font-medium text-[var(--paper-100)]">mail.send</span>
                <span className="text-[var(--jade-400)]">Approved</span>
              </div>
              <div className="text-xs text-[var(--paper-500)]">2 hours ago · Tier 2</div>
            </div>
            <div className="py-3 border-b border-[var(--line)]">
              <div className="flex justify-between mb-1">
                <span className="font-medium text-[var(--paper-100)]">calendar.delete_event</span>
                <span className="text-[var(--danger-400)]">Denied</span>
              </div>
              <div className="text-xs text-[var(--paper-500)]">Yesterday · Tier 2</div>
            </div>
            <div className="py-3">
              <div className="flex justify-between mb-1">
                <span className="font-medium text-[var(--paper-100)]">filesystem.write</span>
                <span className="text-[var(--jade-400)]">Approved</span>
              </div>
              <div className="text-xs text-[var(--paper-500)]">Yesterday · Tier 2</div>
            </div>
          </div>
        </section>
      </div>
    </>
  );
}
