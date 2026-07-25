"use client";
import { Badge } from "../primitives/Badge";
import { Panel } from "../primitives/Panel";
import { ConnectionState, elapsedSeconds, isTerminal, Task, TaskEvent, CancelTaskResponse } from "../../lib/api/contracts";
import { formatDistanceToNowStrict } from "date-fns";

export function ConnectionBadge({
  state,
}: {
  state: ConnectionState;
}) {
  if (state === "connected") {
    return (
      <Badge variant="success" className="animate-pulse">
        Live
      </Badge>
    );
  }
  if (state === "reconnecting") {
    return <Badge variant="warning">Reconnecting...</Badge>;
  }
  if (state === "stale" || state === "offline") {
    return (
      <Badge variant="error">
        {state === "stale" ? "Stale Data" : "Offline"}
      </Badge>
    );
  }
  return null;
}

export function TimelineEventRow({ event }: { event: TaskEvent }) {
  const timeStr = formatDistanceToNowStrict(new Date(event.ts), { addSuffix: true });
  
  return (
    <div className="relative pl-6 pb-6 border-l border-zinc-800 last:border-0 last:pb-0">
      <div className="absolute -left-1.5 top-1.5 h-3 w-3 rounded-full border-2 border-zinc-950 bg-zinc-600" />
      <div className="flex flex-col gap-1">
        <div className="flex items-center gap-2">
          <span className="font-medium text-zinc-100">{event.summary}</span>
          <span className="text-xs text-zinc-500">{timeStr}</span>
        </div>
        
        {/* Safe Metadata Drawer */}
        {(event.capability || Object.keys(event.safe_metadata).length > 0) && (
          <div className="mt-2 flex flex-col gap-1 rounded bg-zinc-900/50 p-3 text-sm text-zinc-400">
            {event.capability && (
              <div className="flex items-center gap-2">
                <span className="text-zinc-500">capability:</span>
                <span className="text-indigo-400">{event.capability}</span>
                {event.operation && <span className="text-zinc-300">.{event.operation}</span>}
              </div>
            )}
            {Object.entries(event.safe_metadata).map(([k, v]) => (
              <div key={k} className="flex gap-2">
                <span className="text-zinc-500">{k}:</span>
                <span className="break-all">{v}</span>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

export function RuntimeHeader({
  task,
  onCancel,
  isCancelling,
  cancelResult,
}: {
  task: Task;
  onCancel: () => void;
  isCancelling: boolean;
  cancelResult: CancelTaskResponse | undefined;
}) {
  const elapsed = elapsedSeconds(task);
  const terminal = isTerminal(task.state);

  return (
    <div className="flex flex-col gap-4 border-b border-zinc-800 pb-6">
      <div className="flex items-start justify-between">
        <div>
          <h1 className="text-xl font-medium text-zinc-100">Task {task.id.slice(-8)}</h1>
          <p className="mt-1 text-sm text-zinc-400">Source: {task.source}</p>
        </div>
        <div className="flex items-center gap-4">
          <Badge variant={terminal ? (task.state === "completed" ? "success" : "error") : "warning"}>
            {task.state}
          </Badge>
          {!terminal && (
            <button
              onClick={onCancel}
              disabled={isCancelling}
              className="rounded bg-zinc-800 px-3 py-1 text-sm font-medium text-zinc-200 hover:bg-zinc-700 disabled:opacity-50"
            >
              {isCancelling ? "Cancelling..." : "Cancel"}
            </button>
          )}
        </div>
      </div>
      <Panel className="bg-zinc-900/50 p-4">
        <p className="font-mono text-sm text-zinc-300">{task.request}</p>
      </Panel>
      <div className="text-sm text-zinc-500">
        Elapsed: {elapsed}s · Created: {new Date(task.created_at).toLocaleTimeString()}
      </div>
      {cancelResult && !cancelResult.accepted && (
        <div className="text-sm text-red-400">{cancelResult.message}</div>
      )}
    </div>
  );
}
