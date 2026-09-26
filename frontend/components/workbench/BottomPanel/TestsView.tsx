"use client";

import { useMemo, useState } from "react";
import { Loader2, FlaskConical, CircleAlert, Play } from "lucide-react";
import { useProjectModel } from "@/features/ide-run/queries";
import { useRunTests, isCommandExecutionDisabled } from "@/features/ide-checks/mutations";
import { hasParsedCounts, type TestReport } from "@/features/ide-checks/contracts";

/**
 * Tests view (Slice 8). Runs the project's real test command through the SAME
 * governed CommandRunner funnel as any shell dispatch — the backend parses
 * pass/fail; this only renders it. HONEST: when the framework was unrecognized the
 * counts are absent ("ran, unparsed"), never fabricated zeros; a denied run says so.
 * Failures are clickable — they open the referenced file in the editor.
 */
export function TestsView({
  workspaceId,
  onOpenFile,
}: {
  workspaceId: string;
  onOpenFile: (path: string) => void;
}) {
  const project = useProjectModel(workspaceId);
  const run = useRunTests(workspaceId);
  const [command, setCommand] = useState("");

  const candidates = useMemo(
    () => project.data?.test_commands ?? [],
    [project.data],
  );

  const submit = (cmd: string) => {
    const trimmed = cmd.trim();
    if (!trimmed || run.isPending) return;
    setCommand(trimmed);
    run.mutate({ command: trimmed });
  };

  if (run.isError && isCommandExecutionDisabled(run.error)) {
    return (
      <div className="wb-checks wb-checks-msg">
        <CircleAlert size={15} />
        Command execution is not enabled on this ATLAS runtime — tests cannot run.
      </div>
    );
  }

  const report = run.data;

  return (
    <div className="wb-checks">
      <div className="wb-checks-bar">
        <input
          className="wb-checks-input"
          value={command}
          placeholder="Test command (e.g. pytest -q)…"
          spellCheck={false}
          autoComplete="off"
          onChange={(e) => setCommand(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              e.preventDefault();
              submit(command);
            }
          }}
        />
        <button
          type="button"
          className="wb-checks-go"
          disabled={run.isPending || !command.trim()}
          onClick={() => submit(command)}
          title="Run tests"
        >
          {run.isPending ? <Loader2 size={13} className="wb-spin" /> : <Play size={13} />}
        </button>
      </div>

      {candidates.length > 0 && !report && !run.isPending ? (
        <div className="wb-checks-candidates">
          <span className="wb-checks-candidates-head">Detected</span>
          {candidates.map((c) => (
            <button
              key={c}
              type="button"
              className="wb-checks-chip"
              onClick={() => submit(c)}
              title={`Run: ${c}`}
            >
              {c}
            </button>
          ))}
        </div>
      ) : null}

      <div className="wb-checks-body">
        {run.isPending ? (
          <p className="wb-checks-hint">
            <Loader2 size={13} className="wb-spin" /> Running tests…
          </p>
        ) : report ? (
          <TestReportView report={report} onOpenFile={onOpenFile} />
        ) : (
          <p className="wb-checks-hint">
            <FlaskConical size={13} /> Run a test command to see structured pass/fail.
          </p>
        )}
      </div>
    </div>
  );
}

function TestReportView({
  report,
  onOpenFile,
}: {
  report: TestReport;
  onOpenFile: (path: string) => void;
}) {
  if (report.denied) {
    return (
      <p className="wb-checks-denied">
        ⚠ denied by policy{report.error ? `: ${report.error}` : ""} — nothing ran.
      </p>
    );
  }
  if (report.error && report.exit_code === null) {
    return <p className="wb-checks-denied">✕ {report.error}</p>;
  }

  const parsed = hasParsedCounts(report);
  return (
    <>
      <div className="wb-checks-summary">
        <span className={report.ok ? "wb-checks-stat is-ok" : "wb-checks-stat is-fail"}>
          {report.ok ? "PASSED" : "FAILED"}
        </span>
        {report.framework ? (
          <span className="wb-checks-badge">{report.framework}</span>
        ) : null}
        {parsed ? (
          <>
            <span className="wb-checks-count is-ok">{report.passed ?? 0} passed</span>
            <span className="wb-checks-count is-fail">{report.failed ?? 0} failed</span>
            {(report.skipped ?? 0) > 0 ? (
              <span className="wb-checks-count is-skip">{report.skipped} skipped</span>
            ) : null}
            <span className="wb-checks-count">{report.total ?? 0} total</span>
          </>
        ) : (
          <span className="wb-checks-count is-skip">
            ran, unparsed (exit {report.exit_code ?? "?"})
          </span>
        )}
        {report.duration_ms > 0 ? (
          <span className="wb-checks-ms">{report.duration_ms} ms</span>
        ) : null}
      </div>

      {report.failures.length > 0 ? (
        <ul className="wb-checks-list">
          {report.failures.map((f, i) => (
            <li key={`${f.name}-${i}`} className="wb-checks-item">
              <button
                type="button"
                className="wb-checks-item-head"
                disabled={!f.file}
                onClick={() => f.file && onOpenFile(f.file)}
                title={f.file ? `Open ${f.file}` : undefined}
              >
                <span className={`wb-checks-outcome is-${f.outcome}`}>{f.outcome}</span>
                <span className="wb-checks-name">{f.name}</span>
                {f.file ? (
                  <span className="wb-checks-loc">
                    {f.file}
                    {f.line !== null ? `:${f.line}` : ""}
                  </span>
                ) : null}
              </button>
              {f.message ? <p className="wb-checks-msg-text">{f.message}</p> : null}
            </li>
          ))}
        </ul>
      ) : null}

      {report.output_tail ? (
        <details className="wb-checks-tail">
          <summary>Output</summary>
          <pre>{report.output_tail}</pre>
        </details>
      ) : null}
    </>
  );
}
