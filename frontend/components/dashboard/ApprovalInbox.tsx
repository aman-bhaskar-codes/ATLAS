"use client";
import Link from 'next/link';
import { usePendingApprovals } from '@/features/trust/queries';
import { ApprovalCard } from '@/components/trust/ApprovalCard';
import { ErrorState } from '@/components/primitives/ErrorState';
import { EmptyState } from '@/components/primitives/EmptyState';
export function ApprovalInbox() {
  const query = usePendingApprovals();
  return <section className="panel"><div className="section-head"><h2>Approval inbox</h2><Link href="/approvals">View all</Link></div>
    <div className="px-4">
      {query.isLoading ? <p role="status">Checking for pending approvals…</p> : query.isError ? <ErrorState title="Failed to load approvals" error={query.error} onRetry={() => void query.refetch()} /> : !query.data?.length ? <EmptyState title="Inbox Zero" description="No pending actions required." /> : query.data.slice(0, 3).map(approval => <ApprovalCard key={approval.id} approval={approval} />)}
    </div>
  </section>;
}
