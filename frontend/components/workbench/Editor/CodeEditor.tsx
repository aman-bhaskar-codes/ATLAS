"use client";

import { useCallback, useMemo, useState } from "react";
import { Save, AlertTriangle, RotateCcw } from "lucide-react";
import { useDocument } from "@/features/workspace/queries";
import { useApplyChange, fullFileReplaceOps } from "@/features/workspace/mutations";

/**
 * Editable code view for one open file (spec §4 Editor Workbench, Slices 2–3).
 *
 * Read path: fetches the REAL document (bytes + version) from
 * GET /ide/workspaces/{id}/document?path=. Write path: a governed full-file
 * `replace` through POST /ide/workspaces/{id}/change carrying the loaded
 * `expected_version`, so a stale write (the file changed on disk since it was
 * opened) is refused honestly rather than clobbering. The draft lives in the
 * parent {@link Workbench} keyed by path, so switching tabs never loses edits.
 */
export function CodeEditor({
  workspaceId,
  path,
  draft,
  onDraftChange,
  onSaved,
}: {
  workspaceId: string;
  path: string;
  /** Unsaved text, or undefined when the buffer matches disk. */
  draft: string | undefined;
  onDraftChange: (path: string, value: string | undefined) => void;
  onSaved: (path: string) => void;
}) {
  const { data: doc, isLoading, isError, error } = useDocument(workspaceId, path);
  const apply = useApplyChange(workspaceId);
  // A stale save pins the conflict to the version it was refused against; when the
  // document reloads to a new version the flag stops matching and clears itself —
  // no effect, no cascading setState.
  const [conflictVersion, setConflictVersion] = useState<string | null>(null);

  const value = draft ?? doc?.content ?? "";
  const dirty = draft !== undefined && draft !== doc?.content;
  const lineCount = useMemo(() => value.split("\n").length, [value]);
  const conflict = !!doc && conflictVersion === doc.version;

  const save = useCallback(() => {
    if (!doc || !dirty || apply.isPending) return;
    setConflictVersion(null);
    apply.mutate(
      {
        path,
        expected_version: doc.version,
        operations: fullFileReplaceOps(value, doc.line_count),
        rationale: "Editor save",
      },
      {
        onSuccess: (result) => {
          if (result.applied) onSaved(path);
          else if (result.stale) setConflictVersion(doc.version);
        },
      },
    );
  }, [doc, dirty, apply, path, value, onSaved]);

  // ⌘S / Ctrl-S saves the focused editor without leaving the workbench.
  const onKeyDown = useCallback(
    (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "s") {
        e.preventDefault();
        save();
      }
    },
    [save],
  );

  if (isLoading) {
    return <div className="wb-panel-body"><p className="wb-placeholder">Opening {path}…</p></div>;
  }
  if (isError || !doc) {
    return (
      <div className="wb-panel-body">
        <p className="wb-placeholder" style={{ color: "var(--danger-400)" }}>
          Could not open {path}
          {error instanceof Error ? `: ${error.message}` : ""}.
        </p>
      </div>
    );
  }

  return (
    <>
      {conflict && (
        <div className="wb-editor-conflict" role="alert">
          <AlertTriangle size={14} />
          <span>
            <strong>{path}</strong> changed on disk since you opened it — your save was
            refused to avoid clobbering it.
          </span>
          <button
            type="button"
            className="wb-conflict-reload"
            onClick={() => {
              onDraftChange(path, undefined);
              apply.reset();
              setConflictVersion(null);
            }}
          >
            <RotateCcw size={12} /> Discard my edits & reload
          </button>
        </div>
      )}

      <textarea
        className="wb-code-edit"
        value={value}
        spellCheck={false}
        onChange={(e) => {
          const next = e.target.value;
          onDraftChange(path, next === doc.content ? undefined : next);
        }}
        onKeyDown={onKeyDown}
        aria-label={`Editing ${path}`}
      />

      <div className="wb-editor-meta">
        <span>{doc.path}</span>
        <span className="wb-meta-accent">{doc.language || "plaintext"}</span>
        <span>{lineCount} lines</span>
        <span>
          version <span className="wb-meta-accent">{doc.version}</span>
        </span>
        <span className="wb-meta-spacer" />
        {apply.isError && (
          <span style={{ color: "var(--danger-400)" }}>
            Save failed: {apply.error.message}
          </span>
        )}
        <span style={{ color: dirty ? "var(--gold-400)" : "var(--paper-600)" }}>
          {dirty ? "unsaved changes" : "saved"}
        </span>
        <button
          type="button"
          className="wb-save-btn"
          disabled={!dirty || apply.isPending}
          onClick={save}
        >
          <Save size={12} /> {apply.isPending ? "Saving…" : "Save"}
        </button>
      </div>
    </>
  );
}
