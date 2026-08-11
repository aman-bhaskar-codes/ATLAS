"use client";

import { useQuery } from "@tanstack/react-query";
import { atlasApi } from "@/lib/api/client";
import Link from "next/link";

import { ErrorState } from "@/components/primitives/ErrorState";
import { EmptyState } from "@/components/primitives/EmptyState";

export function ApprovalInbox() {
  const { data: approvals, isLoading, isError, error } = useQuery({
    queryKey: ["approvals"],
    queryFn: atlasApi.approvals,
    refetchInterval: 5000,
  });

  return (
    <section className="panel">
      <div className="section-head">
        <h2>Approval inbox</h2>
        <Link href="/approvals">View all</Link>
      </div>
      
      {isLoading ? (
        <div className="text-sm text-paper-500 italic py-4 px-4">Checking for pending approvals...</div>
      ) : isError ? (
        <ErrorState title="Failed to load approvals" error={error} />
      ) : !approvals || approvals.length === 0 ? (
        <EmptyState title="Inbox Zero" description="No pending actions required." />
      ) : (
        approvals.slice(0, 3).map((approval: { id: string, summary?: string, tier?: number, operation?: string, capability?: string }) => (
          <div className="approval" key={approval.id}>
            <div className="approval-top">
              <div className="approval-title">{approval.summary || "Pending Action"}</div>
              <span className="badge">Tier {approval.tier || 2}</span>
            </div>
            <div className="approval-desc">
              {approval.operation || "operation"} · {approval.capability || "capability"}
            </div>
            <div className="approval-actions" style={{ display: 'flex', gap: '0.5rem', marginTop: '0.5rem' }}>
              <button className="ghost-btn" style={{ padding: '0.25rem 0.75rem', fontSize: '0.75rem', color: 'var(--jade-400)', border: '1px solid var(--jade-400)' }}>
                Approve
              </button>
              <button className="ghost-btn" style={{ padding: '0.25rem 0.75rem', fontSize: '0.75rem', color: 'var(--danger-400)', border: '1px solid var(--danger-400)' }}>
                Deny
              </button>
            </div>
          </div>
        ))
      )}
    </section>
  );
}
