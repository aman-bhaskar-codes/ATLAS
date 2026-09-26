"use client";

import { CheckCircle2, XCircle, Loader2, FileEdit, TerminalSquare } from "lucide-react";
import type {
  ToolInvocation,
  FileTouch,
  CommandRun,
} from "@/features/agent-runs/projectRunState";

function StatusIcon({ status }: { status: ToolInvocation["status"] }) {
  if (status === "running") return <Loader2 size={12} className="wb-spin" style={{ color: "var(--gold-400)" }} />;
  if (status === "ok") return <CheckCircle2 size={12} style={{ color: "var(--jade-400, #22c55e)" }} />;
  return <XCircle size={12} style={{ color: "var(--danger-400)" }} />;
}

/** A concise label for one governed tool call: `ide · apply_change` etc. */
function toolLabel(t: ToolInvocation): string {
  return t.operation ? `${t.tool} · ${t.operation}` : t.tool;
}

/**
 * The Tools tab: every governed tool call the run drove, newest last, with real
 * status/latency/error from the durable trace. This is the operational record of
 * what the agent did — not raw reasoning.
 */
export function ToolsTab({ tools }: { tools: readonly ToolInvocation[] }) {
  if (tools.length === 0) {
    return <p className="wb-placeholder">No tool calls yet.</p>;
  }
  return (
    <ul className="wb-agent-tools" role="list">
      {tools.map((t) => (
        <li key={t.seq} className="wb-agent-tool">
          <StatusIcon status={t.status} />
          <span className="wb-agent-tool-name">{toolLabel(t)}</span>
          {t.path && <span className="wb-agent-tool-path">{t.path}</span>}
          <span className="wb-meta-spacer" style={{ flex: 1 }} />
          {t.status !== "running" && t.latencyMs > 0 && (
            <span className="wb-agent-tool-ms">{t.latencyMs} ms</span>
          )}
          {t.error && <span className="wb-agent-tool-err" title={t.error}>{t.error}</span>}
        </li>
      ))}
    </ul>
  );
}

/**
 * The Tests tab: commands the agent ran through `ide · run_command` — the same
 * governed funnel the human's Tests/Problems views use. Shows each command with its
 * real status and latency from the durable trace (no parsed counts here; the report
 * lives in the run's tool result). This is the agent's operational command record.
 */
export function TestsTab({ commands }: { commands: readonly CommandRun[] }) {
  if (commands.length === 0) {
    return <p className="wb-placeholder">No commands run by the agent yet.</p>;
  }
  return (
    <ul className="wb-agent-tools" role="list">
      {commands.map((c) => (
        <li key={c.seq} className="wb-agent-tool">
          {c.status === "running" ? (
            <Loader2 size={12} className="wb-spin" style={{ color: "var(--gold-400)" }} />
          ) : c.status === "ok" ? (
            <CheckCircle2 size={12} style={{ color: "var(--jade-400, #22c55e)" }} />
          ) : (
            <XCircle size={12} style={{ color: "var(--danger-400)" }} />
          )}
          <TerminalSquare size={12} style={{ color: "var(--paper-500)", flexShrink: 0 }} />
          <span className="wb-agent-tool-path" style={{ fontFamily: "var(--font-mono)" }}>
            {c.command}
          </span>
          <span className="wb-meta-spacer" style={{ flex: 1 }} />
          {c.status !== "running" && c.latencyMs > 0 && (
            <span className="wb-agent-tool-ms">{c.latencyMs} ms</span>
          )}
          {c.error && (
            <span className="wb-agent-tool-err" title={c.error}>
              {c.error}
            </span>
          )}
        </li>
      ))}
    </ul>
  );
}

/**
 * The Files tab: files the agent actually wrote in this workspace (successful
 * `ide · apply_change` calls). Clicking a row opens it in the editor so the human
 * sees the agent's change against real on-disk bytes.
 */
export function FilesTab({
  files,
  onOpenFile,
}: {
  files: readonly FileTouch[];
  onOpenFile: (path: string) => void;
}) {
  if (files.length === 0) {
    return <p className="wb-placeholder">No files changed by the agent yet.</p>;
  }
  return (
    <ul className="wb-agent-files" role="list">
      {files.map((f) => (
        <li key={f.path}>
          <button type="button" className="wb-agent-file" onClick={() => onOpenFile(f.path)} title={f.path}>
            <FileEdit size={12} style={{ color: "var(--gold-400)" }} />
            <span className="wb-agent-file-path">{f.path}</span>
          </button>
        </li>
      ))}
    </ul>
  );
}
