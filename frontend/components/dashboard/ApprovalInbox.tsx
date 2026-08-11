"use client";

import { useQuery } from "@tanstack/react-query";
import { atlasApi } from "@/lib/api/client";
import Link from "next/link";

export function ApprovalInbox() {
  const { data: approvals, isLoading } = useQuery({
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
        <div className="text-sm text-paper-500 italic py-4">Checking for pending approvals...</div>
      ) : !approvals || approvals.length === 0 ? (
        <div className="text-sm text-paper-500 italic py-4">No pending approvals.</div>
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
            <div className="approval-actions">
              <Link href={`/approvals/${approval.id}`} className="small-btn approve" style={{ textDecoration: 'none' }}>
                Review preview
              </Link>
            </div>
          </div>
        ))
      )}
    </section>
  );
}
