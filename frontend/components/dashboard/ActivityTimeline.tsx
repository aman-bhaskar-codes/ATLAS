"use client";

import { useQuery } from "@tanstack/react-query";
import { atlasApi } from "@/lib/api/client";
import Link from "next/link";
import { Play, Check, XCircle, Clock } from "lucide-react";
import { formatDistanceToNow } from "date-fns";

import { ErrorState } from "@/components/primitives/ErrorState";
import { EmptyState } from "@/components/primitives/EmptyState";

export function ActivityTimeline() {
  const { data: tasks, isLoading, isError, error, refetch } = useQuery({
    queryKey: ["tasks"],
    queryFn: () => atlasApi.tasks(),
    refetchInterval: 5000,
  });

  return (
    <section className="panel">
      <div className="section-head">
        <h2>Recent activity</h2>
        <Link href="/tasks">View all tasks</Link>
      </div>
      <div className="timeline">
        {isLoading ? (
          <div className="event">
            <div className="event-icon"><Clock width={13} /></div>
            <div>
              <div className="event-title text-paper-300">Loading tasks...</div>
            </div>
          </div>
        ) : isError ? (
          <ErrorState title="Timeline offline" error={error} onRetry={() => void refetch()} />
        ) : !tasks || tasks.length === 0 ? (
          <EmptyState title="No recent activity" description="ATLAS is quiet and ready." />
        ) : (
          tasks.slice(0, 5).map((task) => {
            // REMOVED: a `tiers = ["AUTO","CONFIRM","BLOCK","AUTO","AUTO"]` array
            // indexed by row position, rendered as a badge. `TaskSchema` carries no
            // tier — only `TaskEventSchema` does — so every one of those labels was
            // invented, and the third row always claimed "BLOCK". Fabricating a
            // safety classification is the one thing this UI must never do.
            return (
              <div className="event" key={task.id} style={{ alignItems: 'flex-start' }}>
                <div className="event-icon" style={{ marginTop: '0.25rem' }}>
                  {task.state === "completed" ? (
                    <Check width={13} />
                  ) : task.state === "failed" ? (
                    <XCircle width={13} className="text-danger-400" />
                  ) : (
                    <Play width={13} />
                  )}
                </div>
                <div style={{ flex: 1, display: 'flex', flexDirection: 'column', gap: '0.25rem' }}>
                  <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', width: '100%' }}>
                    <Link href={`/tasks/${task.id}`} className="event-title hover:text-gold-400 transition-colors" style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
                      {task.state === "completed" ? "Task completed" : task.state === "failed" ? "Task failed" : "Task active"}
                      <span className="badge" style={{
                        fontSize: '0.65rem',
                        padding: '0.1rem 0.3rem',
                        borderColor: 'var(--line)',
                        color: 'var(--paper-500)',
                        background: 'transparent',
                      }}>
                        {task.source}
                      </span>
                    </Link>
                    <div className="event-time mono">
                      {formatDistanceToNow(new Date(task.created_at), { addSuffix: true })}
                    </div>
                  </div>
                  <div className="event-meta truncate max-w-[250px] sm:max-w-md">{task.request}</div>
                </div>
              </div>
            );
          })
        )}
      </div>
    </section>
  );
}
