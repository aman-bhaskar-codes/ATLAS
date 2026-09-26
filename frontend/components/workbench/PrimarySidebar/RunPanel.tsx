"use client";

import { useMemo, useState } from "react";
import {
  Play,
  Square,
  Loader2,
  CircleAlert,
  ExternalLink,
  TerminalSquare,
} from "lucide-react";
import { useProcesses, useProjectModel } from "@/features/ide-run/queries";
import {
  useStartProcess,
  useStopProcess,
  isCommandExecutionDisabled,
} from "@/features/ide-run/mutations";
import { isProcessTerminal, type DevProcess } from "@/features/ide-run/contracts";

const STATUS_LABEL: Record<DevProcess["status"], string> = {
  pending: "pending",
  starting: "starting",
  running: "running",
  healthy: "healthy",
  unhealthy: "unhealthy",
  exited: "exited",
  failed: "failed",
  killed: "stopped",
};

function statusColor(status: DevProcess["status"]): string {
  if (status === "healthy") return "var(--jade-400, #22c55e)";
  if (status === "running" || status === "starting" || status === "pending") return "var(--gold-400)";
  if (status === "failed" || status === "unhealthy") return "var(--danger-400)";
  return "var(--paper-500, #9ca3af)"; // exited / killed
}

/**
 * Run and Debug sidebar (Slice 7). Offers the workspace's REAL run/build/test
 * command candidates (from the analyzed ProjectModel) and manages long-lived dev
 * processes launched through the SAME SafetyEngine funnel as any shell dispatch.
 * Detected ports are the ones a server announced in its own stdout — rendered as
 * clickable localhost links, never guessed. Nothing here is fabricated: a runtime
 * without command execution says so, and an empty candidate list is honest.
 */
export function RunPanel({
  workspaceId,
  onOpenProcess,
}: {
  workspaceId: string;
  /** Reveal a process's live output in the bottom panel (its id IS a terminal id). */
  onOpenProcess: (processId: string, command: string) => void;
}) {
  const project = useProjectModel(workspaceId);
  const start = useStartProcess(workspaceId);
  const stop = useStopProcess(workspaceId);
  const [custom, setCustom] = useState("");

  // Self-pacing poll: the query refetches only while a process is still live.
  const procsQuery = useProcesses(workspaceId);
  const processes = procsQuery.data?.processes ?? [];

  const candidates = useMemo(() => {
    const pm = project.data;
    if (!pm) return [] as { group: string; command: string }[];
    return [
      ...pm.run_commands.map((c) => ({ group: "run", command: c })),
      ...pm.build_commands.map((c) => ({ group: "build", command: c })),
      ...pm.test_commands.map((c) => ({ group: "test", command: c })),
    ];
  }, [project.data]);

  const launch = (command: string) => {
    const trimmed = command.trim();
    if (!trimmed) return;
    start.mutate(
      { command: trimmed },
      { onSuccess: (proc) => onOpenProcess(proc.id, proc.command) },
    );
  };

  if (start.isError && isCommandExecutionDisabled(start.error)) {
    return (
      <div className="wb-run">
        <p className="wb-placeholder wb-run-disabled">
          <CircleAlert size={14} /> Command execution is not enabled on this ATLAS
          runtime — dev processes cannot run.
        </p>
      </div>
    );
  }

  return (
    <div className="wb-run">
      {/* ── Command candidates ─────────────────────────────────────────────── */}
      <div className="wb-run-section">
        <div className="wb-run-section-head">Run configurations</div>
        {project.isLoading ? (
          <p className="wb-placeholder">Analyzing project…</p>
        ) : candidates.length === 0 ? (
          <p className="wb-placeholder">
            No run/build/test commands detected. Launch one manually below.
          </p>
        ) : (
          <ul className="wb-run-candidates" role="list">
            {candidates.map(({ group, command }) => (
              <li key={`${group}:${command}`} className="wb-run-candidate">
                <button
                  type="button"
                  className="wb-run-launch"
                  onClick={() => launch(command)}
                  disabled={start.isPending}
                  title={`Run: ${command}`}
                >
                  <Play size={12} />
                </button>
                <span className="wb-run-cand-group">{group}</span>
                <code className="wb-run-cand-cmd">{command}</code>
              </li>
            ))}
          </ul>
        )}

        <div className="wb-run-custom">
          <input
            className="wb-run-input"
            value={custom}
            placeholder="Custom command, e.g. npm run dev"
            spellCheck={false}
            autoComplete="off"
            onChange={(e) => setCustom(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                e.preventDefault();
                launch(custom);
                setCustom("");
              }
            }}
          />
          <button
            type="button"
            className="wb-run-custom-go"
            onClick={() => {
              launch(custom);
              setCustom("");
            }}
            disabled={start.isPending || custom.trim() === ""}
          >
            {start.isPending ? <Loader2 size={12} className="wb-spin" /> : <Play size={12} />}
          </button>
        </div>
      </div>

      {/* ── Active / recent processes ──────────────────────────────────────── */}
      <div className="wb-run-section">
        <div className="wb-run-section-head">Processes</div>
        {processes.length === 0 ? (
          <p className="wb-placeholder">No processes started.</p>
        ) : (
          <ul className="wb-run-procs" role="list">
            {processes.map((proc) => {
              const terminal = isProcessTerminal(proc.status);
              return (
                <li key={proc.id} className="wb-run-proc">
                  <div className="wb-run-proc-head">
                    <span
                      className="wb-run-proc-status"
                      style={{ color: statusColor(proc.status) }}
                    >
                      ● {STATUS_LABEL[proc.status]}
                      {terminal && proc.exit_code !== null ? ` (${proc.exit_code})` : ""}
                    </span>
                    <span className="wb-run-proc-actions">
                      <button
                        type="button"
                        className="wb-run-proc-btn"
                        onClick={() => onOpenProcess(proc.id, proc.command)}
                        title="Show output"
                      >
                        <TerminalSquare size={12} />
                      </button>
                      {!terminal && (
                        <button
                          type="button"
                          className="wb-run-proc-btn wb-run-stop"
                          onClick={() => stop.mutate({ processId: proc.id })}
                          disabled={stop.isPending}
                          title="Stop process"
                        >
                          <Square size={12} />
                        </button>
                      )}
                    </span>
                  </div>
                  <code className="wb-run-proc-cmd">{proc.command}</code>
                  {proc.detected_ports.length > 0 && (
                    <div className="wb-run-ports">
                      {proc.detected_ports.map((port) => (
                        <a
                          key={port}
                          className="wb-run-port"
                          href={`http://localhost:${port}`}
                          target="_blank"
                          rel="noopener noreferrer"
                        >
                          <ExternalLink size={11} /> localhost:{port}
                        </a>
                      ))}
                    </div>
                  )}
                </li>
              );
            })}
          </ul>
        )}
      </div>
    </div>
  );
}
