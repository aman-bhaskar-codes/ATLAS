import React from "react";
import type { Task } from "../../lib/api/contracts";
import { Panel } from "../primitives/Panel";
import { TaskStateBadge } from "../runtime/TaskStateBadge";
import { Copy, TerminalSquare } from "lucide-react";
import { IconButton } from "../primitives/IconButton";

interface IntentReceiptProps {
  task: Task;
}

export function IntentReceipt({ task }: IntentReceiptProps) {
  const handleCopyId = () => {
    navigator.clipboard.writeText(task.id);
  };

  return (
    <Panel className="bg-[var(--color-ink-950)] border-dashed">
      <div className="flex items-start justify-between">
        <div className="flex gap-4">
          <div className="mt-1 text-[var(--color-royal-500)]">
            <TerminalSquare className="w-5 h-5" />
          </div>
          <div>
            <div className="flex items-center gap-3 mb-2">
              <h4 className="text-sm font-medium text-[var(--color-paper-100)]">Task Intent Received</h4>
              <TaskStateBadge state={task.state} />
            </div>
            <p className="text-sm text-[var(--color-paper-300)] italic border-l-2 border-[var(--color-line)] pl-3 py-1 my-2">
              {task.request}
            </p>
            <div className="flex items-center gap-4 text-xs text-[var(--color-paper-500)] mt-3">
              <span className="flex items-center gap-1">
                ID: <span className="font-mono text-[var(--color-paper-300)]">{task.id.split("-")[0]}...</span>
                <IconButton size="sm" onClick={handleCopyId} className="h-4 w-4 p-0">
                  <Copy className="w-3 h-3" />
                </IconButton>
              </span>
              <span>Source: {task.source}</span>
              <span>Created: {new Date(task.created_at).toLocaleTimeString()}</span>
            </div>
          </div>
        </div>
      </div>
    </Panel>
  );
}
