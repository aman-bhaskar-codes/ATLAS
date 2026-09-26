"use client";

import { useMemo, useState } from "react";
import { Loader2, ShieldAlert, CircleAlert, Play } from "lucide-react";
import { useCollectDiagnostics, isCommandExecutionDisabled } from "@/features/ide-checks/mutations";
import type { Diagnostic, DiagnosticReport } from "@/features/ide-checks/contracts";

/**
 * Problems view (Slice 8). Runs a lint/type-check command through the SAME governed
 * funnel and renders the normalized diagnostics grouped by file. HONEST: a linter
 * exiting non-zero WITH findings is a normal result, not an error — `error` is set
 * only when the tool could not run at all. Each problem opens its file in the editor.
 */
export function ProblemsView({
  workspaceId,
  onOpenFile,
}: {
  workspaceId: string;
  onOpenFile: (path: string) => void;
}) {
  const collect = useCollectDiagnostics(workspaceId);
  const [command, setCommand] = useState("");

  const submit = (cmd: string) => {
    const trimmed = cmd.trim();
    if (!trimmed || collect.isPending) return;
    setCommand(trimmed);
    collect.mutate({ command: trimmed });
  };

  if (collect.isError && isCommandExecutionDisabled(collect.error)) {
    return (
      <div className="wb-checks wb-checks-msg">
        <CircleAlert size={15} />
        Command execution is not enabled on this ATLAS runtime — diagnostics cannot run.
      </div>
    );
  }

  const report = collect.data;

  return (
    <div className="wb-checks">
      <div className="wb-checks-bar">
        <input
          className="wb-checks-input"
          value={command}
          placeholder="Lint / type-check command (e.g. ruff check ., mypy src, tsc --noEmit)…"
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
          disabled={collect.isPending || !command.trim()}
          onClick={() => submit(command)}
          title="Collect diagnostics"
        >
          {collect.isPending ? <Loader2 size={13} className="wb-spin" /> : <Play size={13} />}
        </button>
      </div>

      <div className="wb-checks-body">
        {collect.isPending ? (
          <p className="wb-checks-hint">
            <Loader2 size={13} className="wb-spin" /> Collecting diagnostics…
          </p>
        ) : report ? (
          <ProblemsReportView report={report} onOpenFile={onOpenFile} />
        ) : (
          <p className="wb-checks-hint">
            <ShieldAlert size={13} /> Run a linter or type-checker to see normalized problems.
          </p>
        )}
      </div>
    </div>
  );
}

function ProblemsReportView({
  report,
  onOpenFile,
}: {
  report: DiagnosticReport;
  onOpenFile: (path: string) => void;
}) {
  const groups = useMemo(() => groupByFile(report.diagnostics), [report.diagnostics]);

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

  return (
    <>
      <div className="wb-checks-summary">
        <span className="wb-checks-count is-fail">{report.errors} errors</span>
        <span className="wb-checks-count is-skip">{report.warnings} warnings</span>
        {report.diagnostics.length === 0 ? (
          <span className="wb-checks-count is-ok">no problems</span>
        ) : null}
        {report.duration_ms > 0 ? (
          <span className="wb-checks-ms">{report.duration_ms} ms</span>
        ) : null}
      </div>

      {groups.length > 0 ? (
        <ul className="wb-checks-list">
          {groups.map(([file, diags]) => (
            <li key={file} className="wb-checks-group">
              <div className="wb-checks-group-file">{file}</div>
              <ul className="wb-checks-diags">
                {diags.map((d, i) => (
                  <li key={`${d.line}-${d.col}-${i}`}>
                    <button
                      type="button"
                      className="wb-checks-diag"
                      onClick={() => onOpenFile(d.file)}
                      title={`Open ${d.file}`}
                    >
                      <span className={`wb-checks-sev is-${d.severity}`}>{d.severity}</span>
                      <span className="wb-checks-loc">
                        {d.line !== null ? `${d.line}` : "—"}
                        {d.col !== null ? `:${d.col}` : ""}
                      </span>
                      <span className="wb-checks-diag-msg">{d.message}</span>
                      {d.code ? <span className="wb-checks-code">{d.code}</span> : null}
                    </button>
                  </li>
                ))}
              </ul>
            </li>
          ))}
        </ul>
      ) : report.output_tail ? (
        <details className="wb-checks-tail" open>
          <summary>Output (no recognized diagnostics)</summary>
          <pre>{report.output_tail}</pre>
        </details>
      ) : null}
    </>
  );
}

/** Group diagnostics by file, preserving first-seen order of both files and rows. */
function groupByFile(diags: readonly Diagnostic[]): Array<[string, Diagnostic[]]> {
  const map = new Map<string, Diagnostic[]>();
  for (const d of diags) {
    const bucket = map.get(d.file);
    if (bucket) bucket.push(d);
    else map.set(d.file, [d]);
  }
  return [...map.entries()];
}
