"use client";

import { useQuery } from "@tanstack/react-query";
import { atlasApi } from "@/lib/api/client";
import Link from "next/link";
import { Play, Check, XCircle, Clock } from "lucide-react";
import { formatDistanceToNow } from "date-fns";

export function ActivityTimeline() {
  const { data: tasks, isLoading } = useQuery({
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
        ) : !tasks || tasks.length === 0 ? (
          <div className="event">
            <div className="event-icon"><Clock width={13} /></div>
            <div>
              <div className="event-title text-paper-300">No recent activity</div>
              <div className="event-meta">ATLAS is quiet and ready.</div>
            </div>
          </div>
        ) : (
          tasks.map((task) => (
            <div className="event" key={task.id}>
              <div className="event-icon">
                {task.state === "completed" ? (
                  <Check width={13} />
                ) : task.state === "failed" ? (
                  <XCircle width={13} className="text-danger-400" />
                ) : (
                  <Play width={13} />
                )}
              </div>
              <div>
                <Link href={`/tasks/${task.id}`} className="event-title hover:text-gold-400 transition-colors">
                  {task.state === "completed" ? "Task completed" : task.state === "failed" ? "Task failed" : "Task active"}
                </Link>
                <div className="event-meta truncate max-w-[250px] sm:max-w-md">{task.request}</div>
              </div>
              <div className="event-time mono">
                {formatDistanceToNow(new Date(task.created_at), { addSuffix: true })}
              </div>
            </div>
          ))
        )}
      </div>
    </section>
  );
}
