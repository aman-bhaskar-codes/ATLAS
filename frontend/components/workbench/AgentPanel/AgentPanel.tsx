"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { useStartRun, useContinueRun } from "@/features/agent-runs/mutations";
import { useAgentRunStream } from "@/features/agent-runs/useAgentRunStream";
import { useRun, useWorkspaceRuns } from "@/features/agent-runs/queries";
import { projectRunState } from "@/features/agent-runs/projectRunState";
import { AgentHeader } from "./AgentHeader";
import { RunComposer } from "./RunComposer";
import { ToolsTab, FilesTab, TestsTab } from "./TraceTabs";
import { VerificationTab } from "./VerificationTab";

type TraceTab = "goal" | "actions" | "tools" | "files" | "tests" | "verify";
const TRACE_TABS: { id: TraceTab; label: string }[] = [
  { id: "goal", label: "Goal" },
  { id: "actions", label: "Plan" },
  { id: "tools", label: "Tools" },
  { id: "files", label: "Files" },
  { id: "tests", label: "Tests" },
];
const VERIFY_TAB: { id: TraceTab; label: string } = { id: "verify", label: "Verify" };

/**
 * The Agent cockpit (spec §4, Slice 5). Starts a governed agent run bound to THIS
 * workspace_id, streams the durable trace over SSE (resumable via Last-Event-ID),
 * and folds it into operational tabs — Goal, Plan (discrete actions), Tools, Files.
 * Every visible datum is backend-derived; there is no fabricated content and no raw
 * chain-of-thought. When the agent writes a file, `onFilesChanged` lets the
 * workbench refresh the editor/tree/git against real on-disk bytes.
 */
export function AgentPanel({
  workspaceId,
  onOpenFile,
  onFilesChanged,
}: {
  workspaceId: string;
  onOpenFile: (path: string) => void;
  onFilesChanged: (paths: string[]) => void;
}) {
  const [runId, setRunId] = useState<string | null>(null);
  const [tab, setTab] = useState<TraceTab>("goal");

  const startRun = useStartRun(workspaceId);
  const continueRun = useContinueRun(workspaceId);
  const { data: runs } = useWorkspaceRuns(workspaceId);
  const { data: run } = useRun(runId);
  const { events, status: stream } = useAgentRunStream(runId);

  const projection = useMemo(() => projectRunState(events), [events]);

  // When the agent writes files, refresh the editor/tree/git against on-disk truth.
  // Keyed on the count of distinct written paths so it fires once per new file.
  const lastFilesLen = useRef(0);
  useEffect(() => {
    if (projection.files.length !== lastFilesLen.current) {
      lastFilesLen.current = projection.files.length;
      if (projection.files.length > 0) {
        onFilesChanged(projection.files.map((f) => f.path));
      }
    }
  }, [projection.files, onFilesChanged]);

  const terminal = projection.lifecycle !== "running" && projection.lifecycle !== "idle";
  const busy = projection.lifecycle === "running";
  const goalText = run?.request ?? "";
  const finalText = run?.result?.final_text ?? "";

  return (
    <>
      <AgentHeader lifecycle={projection.lifecycle} stream={stream} runId={runId} />

      {(
        <div className="wb-agent-tabs" role="tablist">
          {(runId ? [...TRACE_TABS, VERIFY_TAB] : [VERIFY_TAB]).map((t) => (
            <button
              key={t.id}
              type="button"
              role="tab"
              aria-selected={tab === t.id}
              className="wb-agent-tab"
              onClick={() => setTab(t.id)}
            >
              {t.label}
              {t.id === "tools" && projection.tools.length > 0 && (
                <span className="wb-agent-tab-count">{projection.tools.length}</span>
              )}
              {t.id === "files" && projection.files.length > 0 && (
                <span className="wb-agent-tab-count">{projection.files.length}</span>
              )}
              {t.id === "tests" && projection.commands.length > 0 && (
                <span className="wb-agent-tab-count">{projection.commands.length}</span>
              )}
            </button>
          ))}
        </div>
      )}

      <div className="wb-panel-body">
        {tab === "verify" ? (
          <VerificationTab workspaceId={workspaceId} onFilesChanged={onFilesChanged} />
        ) : !runId ? (
          <div className="wb-agent-intro">
            <p className="wb-placeholder">
              No active run. Describe a task below and the agent will work in this
              workspace — every edit and command is governed by the same SafetyEngine
              funnel and streamed here live.
            </p>
            {runs && runs.runs.length > 0 && (
              <div className="wb-agent-recent">
                <div className="wb-agent-recent-head">Recent runs</div>
                {runs.runs.slice(0, 6).map((r) => (
                  <button
                    key={r.run_id}
                    type="button"
                    className="wb-agent-recent-row"
                    onClick={() => {
                      setRunId(r.run_id);
                      setTab("goal");
                    }}
                    title={r.request}
                  >
                    <span className={`wb-agent-dot wb-agent-dot-${r.status}`} />
                    <span className="wb-agent-recent-req">{r.request || "(empty request)"}</span>
                  </button>
                ))}
              </div>
            )}
          </div>
        ) : tab === "goal" ? (
          <div className="wb-agent-goal">
            <div className="wb-agent-goal-label">Goal</div>
            <p className="wb-agent-goal-text">{goalText || "…"}</p>
            {finalText && (
              <>
                <div className="wb-agent-goal-label">Result</div>
                <p className="wb-agent-goal-final">{finalText}</p>
              </>
            )}
            {run?.result?.error && (
              <p className="wb-agent-goal-err">{run.result.error}</p>
            )}
          </div>
        ) : tab === "actions" ? (
          <div className="wb-agent-actions">
            <p className="wb-agent-actions-count">
              {projection.actionCount} action{projection.actionCount === 1 ? "" : "s"} ·{" "}
              {run?.result?.model_calls ?? 0} model call
              {(run?.result?.model_calls ?? 0) === 1 ? "" : "s"}
            </p>
            {projection.currentAction && (
              <div className="wb-agent-current">
                <span className="wb-agent-current-label">Current</span>
                {projection.currentAction.operation
                  ? `${projection.currentAction.tool} · ${projection.currentAction.operation}`
                  : projection.currentAction.tool}
                {projection.currentAction.path ? ` — ${projection.currentAction.path}` : ""}
              </div>
            )}
            <ToolsTab tools={projection.tools} />
          </div>
        ) : tab === "tools" ? (
          <ToolsTab tools={projection.tools} />
        ) : tab === "tests" ? (
          <TestsTab commands={projection.commands} />
        ) : (
          <FilesTab files={projection.files} onOpenFile={onOpenFile} />
        )}
      </div>

      <RunComposer
        mode={terminal ? "continue" : "start"}
        pending={startRun.isPending || continueRun.isPending}
        disabled={busy}
        onSubmit={(request) => {
          if (!runId || !terminal) {
            startRun.mutate(
              { request },
              { onSuccess: (r) => { setRunId(r.run_id); setTab("actions"); } },
            );
          } else {
            continueRun.mutate(
              { runId, request },
              { onSuccess: (r) => { setRunId(r.run_id); setTab("actions"); } },
            );
          }
        }}
      />
      {(startRun.isError || continueRun.isError) && (
        <div className="wb-agent-error">
          {(startRun.error ?? continueRun.error) instanceof Error
            ? (startRun.error ?? continueRun.error)!.message
            : "Failed to start the run."}
        </div>
      )}
    </>
  );
}
