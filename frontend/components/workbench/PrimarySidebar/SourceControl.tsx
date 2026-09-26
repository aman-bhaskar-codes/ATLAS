"use client";

import { useState } from "react";
import { GitBranch, CheckCircle, FileDiff, Plus, Minus, GitCommitVertical } from "lucide-react";
import { useWorkspaceGitStatus } from "@/features/workspace/queries";
import {
  useGitStage,
  useGitUnstage,
  useGitCommit,
  useGitBranch,
} from "@/features/workspace/mutations";
import { isCommandExecutionDisabled } from "@/features/ide-terminal/mutations";
import type { GitOpResult } from "@/features/workspace/contracts";

function stateColor(state: string): string {
  if (state === "M" || state === "AM") return "#eab308";
  if (state === "A" || state === "??") return "var(--jade-400, #22c55e)";
  return "var(--danger-400)";
}

/** A one-line honest report of the last git write: git's own message on failure, a
 * policy refusal, or a success summary (with the new SHA/branch when present). */
function opMessage(r: GitOpResult): { text: string; kind: "ok" | "err" } {
  if (r.denied) return { text: r.error ?? "refused by policy", kind: "err" };
  if (!r.ok) return { text: r.error ?? "git command failed", kind: "err" };
  if (r.commit) return { text: `committed ${r.commit}${r.branch ? ` on ${r.branch}` : ""}`, kind: "ok" };
  if (r.action === "branch" || r.action === "checkout") return { text: `on ${r.branch ?? "branch"}`, kind: "ok" };
  return { text: r.detail || "done", kind: "ok" };
}

/**
 * Source Control sidebar (spec §4 read; Slice 9 write). Renders the REAL git status
 * and drives governed git WRITES — stage/unstage per file + all, commit the index,
 * create/switch a branch — each through POST /ide/workspaces/{id}/git/* (the same
 * SafetyEngine funnel). No fabricated success: a denial or git failure surfaces
 * git's own message. 503 (no command tool) → an honest disabled note.
 */
export function SourceControl({
  workspaceId,
  activeDiffPath,
  onOpenDiff,
}: {
  workspaceId: string;
  /** `null` = the "all changes" diff is open; `undefined` = no diff open. */
  activeDiffPath: string | null | undefined;
  onOpenDiff: (path: string | null) => void;
}) {
  const { data: git, isLoading, isError, error } = useWorkspaceGitStatus(workspaceId);
  const stage = useGitStage(workspaceId);
  const unstage = useGitUnstage(workspaceId);
  const commit = useGitCommit(workspaceId);
  const branch = useGitBranch(workspaceId);

  const [message, setMessage] = useState("");
  const [branchName, setBranchName] = useState("");
  const [lastOp, setLastOp] = useState<GitOpResult | null>(null);

  const busy = stage.isPending || unstage.isPending || commit.isPending || branch.isPending;
  const writeError = [stage.error, unstage.error, commit.error, branch.error].find(Boolean) ?? null;
  const disabled = writeError != null && isCommandExecutionDisabled(writeError);

  if (isLoading) return <p className="wb-placeholder">Checking git status…</p>;
  if (isError) {
    return (
      <p className="wb-placeholder" style={{ color: "var(--danger-400)" }}>
        Failed to load git status{error instanceof Error ? `: ${error.message}` : ""}.
      </p>
    );
  }
  if (!git?.is_git_repo) return <p className="wb-placeholder">Not a git repository.</p>;

  const staged = git.changes.filter((c) => c.staged);
  const unstaged = git.changes.filter((c) => !c.staged);

  function record(p: Promise<GitOpResult>) {
    p.then(setLastOp).catch(() => {
      /* mutation error is surfaced via writeError below */
    });
  }

  return (
    <div className="wb-scm">
      <div className="wb-scm-branch">
        <span style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
          <GitBranch size={13} style={{ color: "var(--gold-400)" }} />
          <strong>{git.branch || "(detached)"}</strong>
        </span>
        <span style={{ display: "inline-flex", gap: 8, fontSize: "0.7rem" }}>
          {git.ahead > 0 && <span style={{ color: "var(--jade-400, #22c55e)" }}>↑{git.ahead}</span>}
          {git.behind > 0 && <span style={{ color: "var(--danger-400)" }}>↓{git.behind}</span>}
          {git.ahead === 0 && git.behind === 0 && (
            <CheckCircle size={13} style={{ color: "var(--jade-400, #22c55e)" }} />
          )}
        </span>
      </div>

      {disabled ? (
        <p className="wb-placeholder">Git operations unavailable (command execution disabled).</p>
      ) : (
        <>
          {/* Branch create / switch */}
          <form
            className="wb-scm-branch-form"
            onSubmit={(e) => {
              e.preventDefault();
              const name = branchName.trim();
              if (name) record(branch.mutateAsync({ name }));
            }}
          >
            <input
              className="wb-scm-input"
              placeholder="new branch name…"
              value={branchName}
              onChange={(e) => setBranchName(e.target.value)}
              disabled={busy}
              aria-label="New branch name"
            />
            <button type="submit" className="wb-scm-btn" disabled={busy || !branchName.trim()}>
              <GitBranch size={12} /> Create
            </button>
          </form>

          {/* Commit box */}
          <form
            className="wb-scm-commit"
            onSubmit={(e) => {
              e.preventDefault();
              const m = message.trim();
              if (m) record(commit.mutateAsync({ message: m }).then((r) => {
                if (r.ok) setMessage("");
                return r;
              }));
            }}
          >
            <textarea
              className="wb-scm-input wb-scm-msg"
              placeholder="Commit message (staged changes)…"
              value={message}
              onChange={(e) => setMessage(e.target.value)}
              rows={2}
              disabled={busy}
              aria-label="Commit message"
            />
            <button
              type="submit"
              className="wb-scm-btn wb-scm-commit-btn"
              disabled={busy || !message.trim() || staged.length === 0}
              title={staged.length === 0 ? "Stage changes first" : "Commit the staged index"}
            >
              <GitCommitVertical size={12} /> Commit {staged.length > 0 ? `(${staged.length})` : ""}
            </button>
          </form>

          {lastOp && (
            <p
              className="wb-scm-op"
              style={{ color: opMessage(lastOp).kind === "ok" ? "var(--jade-400, #22c55e)" : "var(--danger-400)" }}
            >
              {opMessage(lastOp).text}
            </p>
          )}
        </>
      )}

      {git.changes.length === 0 ? (
        <p className="wb-placeholder">Working tree clean.</p>
      ) : (
        <>
          <div className="wb-scm-section-head">
            <button type="button" className="wb-scm-all" aria-pressed={activeDiffPath === null} onClick={() => onOpenDiff(null)}>
              <FileDiff size={13} /> All changes ({git.changes.length})
            </button>
            {!disabled && (
              <button
                type="button"
                className="wb-scm-btn"
                disabled={busy || unstaged.length === 0}
                onClick={() => record(stage.mutateAsync({ all_changes: true }))}
                title="Stage all changes"
              >
                <Plus size={12} /> Stage all
              </button>
            )}
          </div>

          {staged.length > 0 && (
            <ScmGroup
              label="Staged"
              rows={staged}
              activeDiffPath={activeDiffPath}
              onOpenDiff={onOpenDiff}
              disabled={disabled}
              busy={busy}
              action="unstage"
              onAction={(path) => record(unstage.mutateAsync({ paths: [path] }))}
            />
          )}
          {unstaged.length > 0 && (
            <ScmGroup
              label="Changes"
              rows={unstaged}
              activeDiffPath={activeDiffPath}
              onOpenDiff={onOpenDiff}
              disabled={disabled}
              busy={busy}
              action="stage"
              onAction={(path) => record(stage.mutateAsync({ paths: [path] }))}
            />
          )}
        </>
      )}
    </div>
  );
}

function ScmGroup({
  label,
  rows,
  activeDiffPath,
  onOpenDiff,
  disabled,
  busy,
  action,
  onAction,
}: {
  label: string;
  rows: readonly { path: string; state: string; staged: boolean }[];
  activeDiffPath: string | null | undefined;
  onOpenDiff: (path: string | null) => void;
  disabled: boolean;
  busy: boolean;
  action: "stage" | "unstage";
  onAction: (path: string) => void;
}) {
  return (
    <div className="wb-scm-group">
      <p className="wb-scm-group-label">{label}</p>
      <div className="wb-scm-list" role="list">
        {rows.map((c, i) => (
          <div
            key={`${c.path}-${i}`}
            className="wb-scm-rowwrap"
            style={{ borderLeft: c.staged ? "2px solid var(--jade-400, #22c55e)" : "2px solid var(--line)" }}
          >
            <button
              type="button"
              className="wb-scm-row"
              aria-pressed={activeDiffPath === c.path}
              onClick={() => onOpenDiff(c.path)}
              title={c.path}
            >
              <span className="wb-scm-path">{c.path}</span>
              <span style={{ color: stateColor(c.state), fontWeight: 600, fontSize: "0.68rem" }}>{c.state}</span>
            </button>
            {!disabled && (
              <button
                type="button"
                className="wb-scm-rowbtn"
                disabled={busy}
                onClick={() => onAction(c.path)}
                title={action === "stage" ? "Stage file" : "Unstage file"}
                aria-label={action === "stage" ? `Stage ${c.path}` : `Unstage ${c.path}`}
              >
                {action === "stage" ? <Plus size={12} /> : <Minus size={12} />}
              </button>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}
