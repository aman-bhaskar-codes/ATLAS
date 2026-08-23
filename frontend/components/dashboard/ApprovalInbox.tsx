"use client";

import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { atlasApi } from "@/lib/api/client";
import type { Approval } from "@/lib/api/contracts";
import { generateIdempotencyKey } from "@/features/trust/idempotency";
import Link from "next/link";

import { ErrorRow, ErrorState } from "@/components/primitives/ErrorState";
import { EmptyState } from "@/components/primitives/EmptyState";

export function ApprovalInbox() {
  const queryClient = useQueryClient();
  const { data: approvals, isLoading, isError, error, refetch } = useQuery({
    queryKey: ["approvals"],
    queryFn: atlasApi.approvals,
    refetchInterval: 5000,
  });

  const decide = useMutation({
    mutationFn: ({ id, decision }: { id: string; decision: "approve" | "deny" }) =>
      atlasApi.decideApproval(id, decision, generateIdempotencyKey()),
    onSuccess: () => {
      // Refresh the inbox, the trust views, and the runtime status pill (pending count).
      queryClient.invalidateQueries({ queryKey: ["approvals"] });
      queryClient.invalidateQueries({ queryKey: ["trust", "approvals", "pending"] });
      queryClient.invalidateQueries({ queryKey: ["runtimeStatus"] });
    },
  });

  const pendingId =
    decide.isPending && decide.variables ? decide.variables.id : null;

  return (
    <section className="panel">
      <div className="section-head">
        <h2>Approval inbox</h2>
        <Link href="/approvals">View all</Link>
      </div>

      {isLoading ? (
        <div className="text-sm text-paper-500 italic py-4 px-4">Checking for pending approvals...</div>
      ) : isError ? (
        <ErrorState title="Failed to load approvals" error={error} onRetry={() => void refetch()} />
      ) : !approvals || approvals.length === 0 ? (
        <EmptyState title="Inbox Zero" description="No pending actions required." />
      ) : (
        <>
          {/* `error.message` was rendered directly here, which for an AtlasApiError
              reads "ATLAS API 403 on /approvals/<id>/decide: …" — the internal path
              and route shape on screen. ErrorRow goes through describeError, which
              states the reason without the URL. */}
          {decide.isError && (
            <div style={{ padding: "0 0 0.5rem" }}>
              <ErrorRow error={decide.error} />
            </div>
          )}
          {approvals.slice(0, 3).map((approval: Approval) => {
            const rowBusy = pendingId === approval.id;
            return (
              <div className="approval" key={approval.id}>
                <div className="approval-top">
                  <div className="approval-title">{approval.prompt || approval.operation}</div>
                  <span className="badge">Tier {approval.tier}</span>
                </div>
                <div className="approval-desc">
                  {approval.operation} · {approval.capability}
                </div>
                <div className="approval-actions" style={{ display: "flex", gap: "0.5rem", marginTop: "0.5rem" }}>
                  <button
                    className="ghost-btn"
                    onClick={() => decide.mutate({ id: approval.id, decision: "approve" })}
                    disabled={decide.isPending}
                    style={{
                      padding: "0.25rem 0.75rem",
                      fontSize: "0.75rem",
                      color: "var(--jade-400)",
                      border: "1px solid var(--jade-400)",
                      opacity: decide.isPending ? 0.6 : 1,
                    }}
                  >
                    {rowBusy ? "Working…" : "Approve"}
                  </button>
                  <button
                    className="ghost-btn"
                    onClick={() => decide.mutate({ id: approval.id, decision: "deny" })}
                    disabled={decide.isPending}
                    style={{
                      padding: "0.25rem 0.75rem",
                      fontSize: "0.75rem",
                      color: "var(--danger-400)",
                      border: "1px solid var(--danger-400)",
                      opacity: decide.isPending ? 0.6 : 1,
                    }}
                  >
                    Deny
                  </button>
                </div>
              </div>
            );
          })}
        </>
      )}
    </section>
  );
}
