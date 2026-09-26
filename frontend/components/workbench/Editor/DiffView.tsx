"use client";

import { useMemo } from "react";
import { X } from "lucide-react";
import { useGitDiff } from "@/features/workspace/queries";
import { splitPatch, classifyDiffLine, type DiffLineKind } from "@/lib/ide/parsePatch";

const LINE_COLOR: Record<DiffLineKind, string> = {
  add: "var(--jade-400, #22c55e)",
  del: "var(--danger-400)",
  hunk: "var(--royal-400, #6ea8fe)",
  meta: "var(--paper-500)",
  context: "var(--paper-300)",
};

const LINE_BG: Partial<Record<DiffLineKind, string>> = {
  add: "color-mix(in srgb, var(--jade-400, #22c55e) 10%, transparent)",
  del: "color-mix(in srgb, var(--danger-400) 10%, transparent)",
};

/**
 * A read-only unified-diff view over GET /ide/workspaces/{id}/git/diff (Slice 4).
 * When `path` is set it isolates that file's section from the working-tree patch;
 * otherwise it shows every changed file. No fabricated content — a clean tree or a
 * non-git root render honest empty states.
 */
export function DiffView({
  workspaceId,
  path,
  onClose,
}: {
  workspaceId: string;
  path: string | null;
  onClose: () => void;
}) {
  const { data, isLoading, isError, error } = useGitDiff(workspaceId, false);

  const patchText = useMemo(() => {
    if (!data?.patch) return "";
    if (!path) return data.patch;
    const section = splitPatch(data.patch).find((s) => s.path === path);
    return section?.body ?? "";
  }, [data, path]);

  const lines = useMemo(() => patchText.split("\n"), [patchText]);

  return (
    <>
      <div className="wb-panel-head">
        <span>Diff — {path ?? "Working tree"}</span>
        <span className="wb-meta-spacer" style={{ flex: 1 }} />
        <button
          type="button"
          className="wb-tab-close"
          aria-label="Close diff"
          onClick={onClose}
        >
          <X size={13} />
        </button>
      </div>
      <div className="wb-code">
        {isLoading ? (
          <p className="wb-placeholder" style={{ padding: 14 }}>Loading diff…</p>
        ) : isError ? (
          <p className="wb-placeholder" style={{ padding: 14, color: "var(--danger-400)" }}>
            Failed to load diff{error instanceof Error ? `: ${error.message}` : ""}.
          </p>
        ) : !data?.is_git_repo ? (
          <p className="wb-placeholder" style={{ padding: 14 }}>Not a git repository.</p>
        ) : patchText.trim() === "" ? (
          <p className="wb-placeholder" style={{ padding: 14 }}>
            {path ? `No working-tree changes in ${path}.` : "Working tree clean."}
          </p>
        ) : (
          <pre className="wb-diff" aria-label="Unified diff">
            {lines.map((line, i) => {
              const kind = classifyDiffLine(line);
              return (
                <div
                  key={i}
                  className="wb-diff-line"
                  style={{ color: LINE_COLOR[kind], background: LINE_BG[kind] }}
                >
                  {line || " "}
                </div>
              );
            })}
          </pre>
        )}
      </div>
    </>
  );
}
