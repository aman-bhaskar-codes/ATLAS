"use client";

import { GitBranch, FolderGit2, CircleDot } from "lucide-react";
import type { Workspace, GitStatus } from "@/features/workspace/contracts";

/**
 * The workbench status bar (spec §4). Slice-1 content is real workspace identity
 * from the backend: name, root path, and — when the workspace is a git repo —
 * the live branch. Nothing here is fabricated; a field is shown only when its
 * backend source has answered.
 */
export function StatusBar({
  workspace,
  git,
}: {
  workspace: Workspace;
  git: GitStatus | null | undefined;
}) {
  return (
    <footer className="wb-status" role="contentinfo">
      <span className="wb-status-item wb-status-accent">
        <FolderGit2 size={12} />
        <strong>{workspace.name}</strong>
      </span>
      <span className="wb-status-item mono" title={workspace.root_paths[0]}>
        {workspace.root_paths[0]}
      </span>
      {git?.is_git_repo && (
        <span className="wb-status-item">
          <GitBranch size={12} />
          {git.branch || "(detached)"}
          {git.ahead > 0 && <span>↑{git.ahead}</span>}
          {git.behind > 0 && <span>↓{git.behind}</span>}
        </span>
      )}
      <span className="wb-status-spacer" />
      <span className="wb-status-item" title="Workspace id">
        <CircleDot size={11} />
        {workspace.workspace_id.slice(0, 8)}
      </span>
    </footer>
  );
}
