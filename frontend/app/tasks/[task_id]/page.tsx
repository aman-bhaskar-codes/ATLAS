"use client";

import { use } from "react";
import { useTaskRuntime } from "../../../features/runtime-console/useTaskRuntime";
import { useTaskEvents } from "../../../features/runtime-console/useTaskEvents";
import { useCancelTask } from "../../../features/runtime-console/useCancelTask";
import {
  ConnectionBadge,
  RuntimeHeader,
  TimelineEventRow,
} from "../../../components/runtime/LiveRunPage";

export default function TaskLiveRunPage({
  params,
}: {
  params: Promise<{ task_id: string }>;
}) {
  const { task_id } = use(params);

  const { task, isTaskLoading, taskError } = useTaskRuntime(task_id);
  const { events, connection, lastSyncedAt } = useTaskEvents(
    task_id,
    task?.state ?? null,
  );
  const { cancelTask, isCancelling, result: cancelResult } = useCancelTask(task_id);

  if (isTaskLoading) {
    return <div className="p-8 text-zinc-500">Loading task...</div>;
  }

  if (taskError || !task) {
    return (
      <div className="p-8 text-red-400">
        Error loading task: {taskError?.message ?? "Not found"}
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-4xl p-6 md:p-12">
      <div className="mb-6 flex items-center justify-between">
        <div className="flex items-center gap-4">
          <ConnectionBadge state={connection} />
          {lastSyncedAt && (
            <span className="text-xs text-zinc-600">
              Last sync: {new Date(lastSyncedAt).toLocaleTimeString()}
            </span>
          )}
        </div>
      </div>

      <RuntimeHeader
        task={task}
        onCancel={cancelTask}
        isCancelling={isCancelling}
        cancelResult={cancelResult}
      />

      <div className="mt-8">
        <h2 className="mb-6 text-sm font-medium text-zinc-500 uppercase tracking-wider">
          Execution Trace
        </h2>
        {events.length === 0 ? (
          <p className="text-sm text-zinc-600">Waiting for events...</p>
        ) : (
          <div className="flex flex-col">
            {events.map((evt) => (
              <TimelineEventRow
                key={evt.event_id}
                event={evt}
              />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
