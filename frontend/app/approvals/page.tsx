"use client";

import React from 'react';
import Link from 'next/link';
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

      <div className="grid-cols-panel" style={{ display: 'grid', gap: '2rem' }}>
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
              description="No actions are waiting for your approval. New confirmation requests appear here while their original action is suspended."
            />
          )}
        </section>

        <section className="panel">
          <div className="section-head"><h2>Decision history</h2></div>
          <p className="px-4 py-4 text-sm">Recorded safety decisions are available in the <Link href="/audit">audit log</Link>. Expired requests never resume automatically.</p>
        </section>
      </div>
    </>
  );
}
