"use client";

import { useMemo, useState } from "react";
import {
  Play,
  Loader2,
  CircleAlert,
  CheckCircle2,
  XCircle,
  Camera,
  History,
  RotateCcw,
} from "lucide-react";
import { useProjectModel } from "@/features/ide-run/queries";
import { useRunTests, useCollectDiagnostics } from "@/features/ide-checks/mutations";
import { hasParsedCounts, type TestReport, type DiagnosticReport } from "@/features/ide-checks/contracts";
import { useCheckpoints } from "@/features/ide-checkpoints/queries";
import {
  useCreateCheckpoint,
  useRestoreCheckpoint,
  isCommandExecutionDisabled,
} from "@/features/ide-checkpoints/mutations";

/**
 * Verification + Checkpoints tab (Slice 10). Two real, backend-sourced concerns:
 *
 *  1. VERIFICATION — runs the workspace's REAL test and build/lint command
 *     candidates (from the analyzed ProjectModel) through the SAME SafetyEngine
 *     funnel and renders the structured pass/fail + problems the backend parsed.
 *     Counts are shown only when the framework was recognized; otherwise it says
 *     "ran, unparsed" — never a fabricated green.
 *
 *  2. CHECKPOINTS — snapshot the working tree under a hidden `refs/atlas/checkpoints/*`
 *     ref (side-effect free) and restore one. Restore rewrites on-disk files, so its
 *     mutation invalidates the editor/tree/git; `onFilesChanged` lets the workbench
 *     refresh any open editors against the restored bytes.
 *
 * Nothing here is fabricated: a runtime without command execution says so honestly.
 */
export function VerificationTab({
  workspaceId,
  onFilesChanged,
}: {
  workspaceId: string;
  onFilesChanged: (paths: string[]) => void;
}) {
  const project = useProjectModel(workspaceId);
  const runTests = useRunTests(workspaceId);
  const collect = useCollectDiagnostics(workspaceId);

  const checkpoints = useCheckpoints(workspaceId);
  const snapshot = useCreateCheckpoint(workspaceId);
  const restore = useRestoreCheckpoint(workspaceId);

  const [label, setLabel] = useState("");

  const testCmds = project.data?.test_commands ?? [];
  const buildCmds = project.data?.build_commands ?? [];

  const testReport = runTests.data;
  const diag = collect.data;

  const disabled = useMemo(
    () =>
      isCommandExecutionDisabled(runTests.error) ||
      isCommandExecutionDisabled(collect.error) ||
      isCommandExecutionDisabled(snapshot.error) ||
      (checkpoints.isError && isCommandExecutionDisabled(checkpoints.error)),
    [runTests.error, collect.error, snapshot.error, checkpoints.isError, checkpoints.error],
  );

  if (disabled) {
    return (
      <div className="wb-verify">
        <p className="wb-placeholder wb-verify-disabled">
          <CircleAlert size={14} /> Command execution is not enabled on this ATLAS
          runtime — verification and checkpoints are unavailable.
        </p>
      </div>
    );
  }

  const doRestore = (checkpointId: string) => {
    restore.mutate(
      { checkpointId },
      { onSuccess: (r) => { if (r.ok) onFilesChanged([]); } },
    );
  };

  return (
    <div className="wb-verify">
      {/* ── Verification signals ───────────────────────────────────────────── */}
      <section className="wb-verify-section">
        <div className="wb-verify-head">Verification</div>

        <div className="wb-verify-cmds">
          <span className="wb-verify-cmds-label">Tests</span>
          {project.isLoading ? (
            <span className="wb-placeholder">Analyzing project…</span>
          ) : testCmds.length === 0 ? (
            <span className="wb-placeholder">No test command detected.</span>
          ) : (
            testCmds.map((c) => (
              <button
                key={c}
                type="button"
                className="wb-verify-run"
                onClick={() => runTests.mutate({ command: c, timeoutS: 300 })}
                disabled={runTests.isPending}
                title={`Run tests: ${c}`}
              >
                {runTests.isPending ? <Loader2 size={12} className="wb-spin" /> : <Play size={12} />}
                <code>{c}</code>
              </button>
            ))
          )}
        </div>

        {testReport && <TestSummary report={testReport} />}
        {runTests.isError && !isCommandExecutionDisabled(runTests.error) && (
          <p className="wb-verify-err">{runTests.error.message}</p>
        )}

        <div className="wb-verify-cmds">
          <span className="wb-verify-cmds-label">Build / lint</span>
          {buildCmds.length === 0 ? (
            <span className="wb-placeholder">No build command detected.</span>
          ) : (
            buildCmds.map((c) => (
              <button
                key={c}
                type="button"
                className="wb-verify-run"
                onClick={() => collect.mutate({ command: c, timeoutS: 300 })}
                disabled={collect.isPending}
                title={`Run: ${c}`}
              >
                {collect.isPending ? <Loader2 size={12} className="wb-spin" /> : <Play size={12} />}
                <code>{c}</code>
              </button>
            ))
          )}
        </div>

        {diag && <DiagnosticsSummary report={diag} />}
        {collect.isError && !isCommandExecutionDisabled(collect.error) && (
          <p className="wb-verify-err">{collect.error.message}</p>
        )}
      </section>

      {/* ── Checkpoints ────────────────────────────────────────────────────── */}
      <section className="wb-verify-section">
        <div className="wb-verify-head">
          <History size={13} /> Checkpoints
        </div>

        <div className="wb-verify-snap">
          <input
            className="wb-verify-label-input"
            placeholder="Label (optional)"
            value={label}
            onChange={(e) => setLabel(e.target.value)}
            disabled={snapshot.isPending}
          />
          <button
            type="button"
            className="wb-verify-snap-btn"
            onClick={() =>
              snapshot.mutate({ label: label.trim() }, { onSuccess: () => setLabel("") })
            }
            disabled={snapshot.isPending}
            title="Snapshot the current working tree"
          >
            {snapshot.isPending ? <Loader2 size={12} className="wb-spin" /> : <Camera size={12} />}
            Snapshot
          </button>
        </div>

        {snapshot.isError && !isCommandExecutionDisabled(snapshot.error) && (
          <p className="wb-verify-err">{snapshot.error.message}</p>
        )}
        {restore.isError && (
          <p className="wb-verify-err">{restore.error.message}</p>
        )}
        {restore.data && !restore.data.ok && (
          <p className="wb-verify-err">{restore.data.error ?? "Restore failed."}</p>
        )}

        {checkpoints.isLoading ? (
          <p className="wb-placeholder">Loading checkpoints…</p>
        ) : (checkpoints.data?.checkpoints.length ?? 0) === 0 ? (
          <p className="wb-placeholder">No checkpoints yet. Snapshot before a risky edit.</p>
        ) : (
          <ul className="wb-verify-cps" role="list">
            {checkpoints.data!.checkpoints.map((cp) => (
              <li key={cp.checkpoint_id} className="wb-verify-cp">
                <code className="wb-verify-cp-sha">{cp.commit}</code>
                <span className="wb-verify-cp-label">{cp.label || cp.checkpoint_id}</span>
                <button
                  type="button"
                  className="wb-verify-cp-restore"
                  onClick={() => doRestore(cp.checkpoint_id)}
                  disabled={restore.isPending}
                  title="Restore this checkpoint over the working tree"
                >
                  {restore.isPending && restore.variables?.checkpointId === cp.checkpoint_id ? (
                    <Loader2 size={12} className="wb-spin" />
                  ) : (
                    <RotateCcw size={12} />
                  )}
                </button>
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}

/** Structured pass/fail from one governed test run — honest counts, never fabricated. */
function TestSummary({ report }: { report: TestReport }) {
  const parsed = hasParsedCounts(report);
  const green = report.ok && (report.failed ?? 0) === 0;
  return (
    <div className="wb-verify-result">
      <div className="wb-verify-result-head">
        {green ? (
          <CheckCircle2 size={13} style={{ color: "var(--jade-400, #22c55e)" }} />
        ) : (
          <XCircle size={13} style={{ color: "var(--danger-400)" }} />
        )}
        <span>{report.framework ?? "tests"}</span>
        {report.denied ? (
          <span className="wb-verify-badge wb-verify-badge-deny">denied</span>
        ) : parsed ? (
          <span className="wb-verify-counts">
            {report.passed ?? 0} passed · {report.failed ?? 0} failed
            {report.skipped ? ` · ${report.skipped} skipped` : ""}
          </span>
        ) : (
          <span className="wb-verify-counts">ran, unparsed (exit {report.exit_code ?? "?"})</span>
        )}
      </div>
      {report.failures.length > 0 && (
        <ul className="wb-verify-failures" role="list">
          {report.failures.slice(0, 8).map((f, i) => (
            <li key={`${f.name}:${i}`} className="wb-verify-failure">
              <code>{f.name}</code>
              {f.message && <span className="wb-verify-failure-msg">{f.message}</span>}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

/** Normalized problems from one governed lint/type-check run. */
function DiagnosticsSummary({ report }: { report: DiagnosticReport }) {
  const clean = report.errors === 0 && report.warnings === 0;
  return (
    <div className="wb-verify-result">
      <div className="wb-verify-result-head">
        {clean ? (
          <CheckCircle2 size={13} style={{ color: "var(--jade-400, #22c55e)" }} />
        ) : (
          <CircleAlert size={13} style={{ color: "var(--gold-400)" }} />
        )}
        {report.denied ? (
          <span className="wb-verify-badge wb-verify-badge-deny">denied</span>
        ) : (
          <span className="wb-verify-counts">
            {report.errors} error{report.errors === 1 ? "" : "s"} · {report.warnings} warning
            {report.warnings === 1 ? "" : "s"}
          </span>
        )}
      </div>
      {report.diagnostics.length > 0 && (
        <ul className="wb-verify-failures" role="list">
          {report.diagnostics.slice(0, 8).map((d, i) => (
            <li key={`${d.file}:${d.line}:${i}`} className="wb-verify-failure">
              <code>
                {d.file}
                {d.line ? `:${d.line}` : ""}
              </code>
              <span className="wb-verify-failure-msg">{d.message}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
