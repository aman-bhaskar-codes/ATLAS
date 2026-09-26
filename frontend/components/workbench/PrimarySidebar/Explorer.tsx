"use client";

import { useMemo } from "react";
import { Folder, FileCode } from "lucide-react";
import { useWorkspaceTree } from "@/features/workspace/queries";
import type { FileNode } from "@/features/workspace/contracts";

/**
 * The Explorer (spec §4 Primary Sidebar). It renders the REAL workspace tree from
 * GET /ide/workspaces/{id}/tree — no fabricated entries. Files are clickable and
 * open in the editor via `onOpenFile`; directories are shown for structure only
 * (recursive expand/collapse is a later refinement). Indentation is derived from
 * the workspace-relative path depth so a flat node list reads as a tree.
 */
export function Explorer({
  workspaceId,
  activePath,
  onOpenFile,
}: {
  workspaceId: string;
  activePath: string | null;
  onOpenFile: (node: FileNode) => void;
}) {
  const { data, isLoading, isError, error } = useWorkspaceTree(workspaceId);

  const nodes = useMemo(() => {
    const list = data?.nodes ?? [];
    // Directories first, then lexicographic by path — a stable, tree-like order.
    return [...list].sort((a, b) => {
      if (a.is_dir !== b.is_dir) return a.is_dir ? -1 : 1;
      return a.path.localeCompare(b.path);
    });
  }, [data]);

  if (isLoading) {
    return <p className="wb-placeholder">Loading tree…</p>;
  }
  if (isError) {
    return (
      <p className="wb-placeholder" style={{ color: "var(--danger-400)" }}>
        Failed to load tree{error instanceof Error ? `: ${error.message}` : ""}.
      </p>
    );
  }
  if (nodes.length === 0) {
    return <p className="wb-placeholder">Tree is empty.</p>;
  }

  return (
    <div className="wb-tree" role="tree" aria-label="Workspace files">
      {nodes.map((node) => {
        const depth = node.path.split("/").filter(Boolean).length - 1;
        const indent = 10 + Math.max(0, depth) * 12;
        if (node.is_dir) {
          return (
            <div
              key={node.path}
              className="wb-tree-row is-dir"
              role="treeitem"
              aria-selected={false}
              style={{ paddingLeft: indent }}
            >
              <Folder size={14} />
              <span className="wb-tree-label">{node.name}</span>
            </div>
          );
        }
        return (
          <button
            key={node.path}
            type="button"
            className="wb-tree-row"
            role="treeitem"
            aria-selected={activePath === node.path}
            style={{ paddingLeft: indent }}
            onClick={() => onOpenFile(node)}
            title={node.path}
          >
            <FileCode size={14} style={{ color: "var(--paper-500)", flexShrink: 0 }} />
            <span className="wb-tree-label">{node.name}</span>
          </button>
        );
      })}
    </div>
  );
}
