"use client";

import { useQuery, useMutation } from "@tanstack/react-query";
import { atlasApi } from "../lib/api/client";
import { RuntimeHealthPanel } from "../components/runtime/RuntimeHealthPanel";
import { CommandComposer } from "../components/command/CommandComposer";
import { useRouter } from "next/navigation";
import { TerminalSquare } from "lucide-react";

export default function Dashboard() {
  const router = useRouter();

  const { data: status, isLoading: isLoadingStatus, error: statusError } = useQuery({
    queryKey: ["runtimeStatus"],
    queryFn: atlasApi.runtimeStatus,
    refetchInterval: 5000,
  });

  const { data: health, isLoading: isLoadingHealth } = useQuery({
    queryKey: ["runtimeHealth"],
    queryFn: atlasApi.runtimeHealth,
    refetchInterval: 15000,
  });

  // Approvals are deferred to Phase Three, so we safely handle empty array.
  const { data: approvals, isLoading: isLoadingApprovals } = useQuery({
    queryKey: ["approvals"],
    queryFn: atlasApi.approvals,
    refetchInterval: 5000,
  });

  const { mutate: submitCommand, isPending } = useMutation({
    mutationFn: (request: string) =>
      atlasApi.createTask({
        request,
        idempotency_key: crypto.randomUUID(),
      }),
    onSuccess: (task) => {
      // Route immediately to the Live Run Console for the new task
      router.push(`/tasks/${encodeURIComponent(task.id)}`);
    },
  });

  return (
    <main className="min-h-screen max-w-6xl mx-auto p-4 md:p-8 space-y-8 pb-32">
      
      {/* Header */}
      <header className="flex items-center justify-between pb-6 border-b border-[var(--color-line)]">
        <div className="flex items-center gap-3">
          <div className="bg-[var(--color-royal-500)] p-2 rounded">
            <TerminalSquare className="w-6 h-6 text-white" />
          </div>
          <div>
            <h1 className="text-xl font-semibold tracking-tight text-[var(--color-paper-100)]">ATLAS Control Plane</h1>
            <p className="text-sm text-[var(--color-paper-300)]">Elite AI Engineering Ecosystem</p>
          </div>
        </div>
      </header>

      {/* System Posture */}
      <RuntimeHealthPanel 
        status={status} 
        health={health} 
        isLoading={isLoadingStatus || isLoadingHealth}
        error={statusError || undefined}
      />

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-8">
        
        {/* Main Column */}
        <div className="lg:col-span-2 space-y-8">
          
          <section>
            <h2 className="text-lg font-medium text-[var(--color-paper-100)] mb-4">Command Composer</h2>
            <CommandComposer 
              onSubmit={(req) => submitCommand(req)} 
              isLoading={isPending}
            />
          </section>

        </div>

        {/* Sidebar */}
        <div className="space-y-8">
          
          <section>
            <div className="flex items-center justify-between mb-4">
              <h2 className="text-lg font-medium text-[var(--color-paper-100)] flex items-center gap-2">
                Approvals Queue
                {approvals && approvals.length > 0 && (
                  <span className="bg-[var(--color-gold-500)] text-[var(--color-ink-950)] text-xs font-bold px-2 py-0.5 rounded-full">
                    {approvals.length}
                  </span>
                )}
              </h2>
            </div>
            
            <div className="space-y-4">
              {isLoadingApprovals ? (
                <div className="animate-pulse h-32 bg-[var(--color-ink-900)] border border-[var(--color-line)] rounded-[var(--radius-md)]"></div>
              ) : approvals && approvals.length > 0 ? (
                <div className="text-sm">
                  {approvals.length} approvals waiting. <a href="/approvals" className="text-[var(--gold-400)]">Go to inbox &rarr;</a>
                </div>
              ) : (
                <div className="text-sm text-[var(--color-paper-300)] italic p-4 text-center border border-dashed border-[var(--color-line)] rounded-[var(--radius-md)]">
                  No pending approvals.
                </div>
              )}
            </div>
          </section>

          <section>
            <h2 className="text-lg font-medium text-[var(--color-paper-100)] mb-4">Trust Center</h2>
            <div className="flex flex-col gap-2">
              <a href="/tasks" className="p-3 bg-[var(--ink-850)] border border-[var(--line)] rounded-md text-sm text-[var(--paper-100)] hover:border-[var(--gold-400)] transition-colors">
                Tasks & History
              </a>
              <a href="/approvals" className="p-3 bg-[var(--ink-850)] border border-[var(--line)] rounded-md text-sm text-[var(--paper-100)] hover:border-[var(--gold-400)] transition-colors">
                Approval Inbox
              </a>
              <a href="/memory" className="p-3 bg-[var(--ink-850)] border border-[var(--line)] rounded-md text-sm text-[var(--paper-100)] hover:border-[var(--gold-400)] transition-colors">
                Semantic Memory
              </a>
              <a href="/audit" className="p-3 bg-[var(--ink-850)] border border-[var(--line)] rounded-md text-sm text-[var(--paper-100)] hover:border-[var(--gold-400)] transition-colors">
                Audit Trail
              </a>
            </div>
          </section>

        </div>
      </div>
    </main>
  );
}

