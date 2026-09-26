"use client";

import { useCallback, useMemo, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, Files, Search, GitBranch, Play } from "lucide-react";
import { useWorkspace, useWorkspaceGitStatus } from "@/features/workspace/queries";
import type { FileNode } from "@/features/workspace/contracts";
import { AtlasApiError } from "@/lib/api/client";
import { ActivityBar, type ActivityView } from "./ActivityBar";
import { StatusBar } from "./StatusBar";
import { Explorer } from "./PrimarySidebar/Explorer";
import { SourceControl } from "./PrimarySidebar/SourceControl";
import { RunPanel } from "./PrimarySidebar/RunPanel";
import { EditorTabs, type OpenTab } from "./Editor/EditorTabs";
import { CodeEditor } from "./Editor/CodeEditor";
import { DiffView } from "./Editor/DiffView";
import { AgentPanel } from "./AgentPanel/AgentPanel";
import { TerminalView } from "./BottomPanel/TerminalView";
import { ProcessOutputView } from "./BottomPanel/ProcessOutputView";
import { TestsView } from "./BottomPanel/TestsView";
import { ProblemsView } from "./BottomPanel/ProblemsView";

function isSubsystemDisabled(error: unknown): boolean {
  return error instanceof AtlasApiError && error.status === 503;
}

const SIDEBAR_TITLE: Record<ActivityView, string> = {
  explorer: "Explorer",
  search: "Search",
  scm: "Source Control",
  run: "Run and Debug",
};

/**
 * The Development Workbench shell (spec §3/§4). It resolves the workspace from the
 * SAME backend list the Command Center reads (no independent frontend state), then
 * lays out the VS Code-class regions. Slice-1 renders the frame plus real
 * workspace identity; Explorer/Editor/Agent/Bottom are labelled regions that later
 * slices fill with live data. Nothing shows fabricated content.
 */
export function Workbench({ workspaceId }: { workspaceId: string }) {
  const [view, setView] = useState<ActivityView>("explorer");
  const [agentOpen, setAgentOpen] = useState(true);
  const [tabs, setTabs] = useState<OpenTab[]>([]);
  const [activePath, setActivePath] = useState<string | null>(null);
  // Unsaved editor buffers keyed by workspace-relative path. An entry exists only
  // while the buffer differs from disk, so its key set IS the dirty-tab set and it
  // survives tab switches (the editor for an inactive tab is unmounted).
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const dirtyPaths = useMemo(() => new Set(Object.keys(drafts)), [drafts]);
  // When a diff is open it takes over the editor area. `diffPath === null` with
  // `diffOpen` means the whole working-tree diff; a string isolates one file.
  const [diffOpen, setDiffOpen] = useState(false);
  const [diffPath, setDiffPath] = useState<string | null>(null);
  // Bottom panel: an interactive terminal, or the read-only output of a managed
  // dev process the user opened from the Run panel (its id IS a terminal id).
  const [procView, setProcView] = useState<{ id: string; command: string } | null>(null);
  const [bottomTab, setBottomTab] = useState<
    "terminal" | "process" | "problems" | "tests"
  >("terminal");

  const openProcess = useCallback((id: string, command: string) => {
    setProcView({ id, command });
    setBottomTab("process");
  }, []);

  const { data: workspace, isLoading, isError, error } = useWorkspace(workspaceId);
  const { data: git } = useWorkspaceGitStatus(workspace ? workspaceId : null);
  const queryClient = useQueryClient();

  // The agent wrote files in this workspace: refresh the editor buffers (for any
  // open tab it touched), the tree, and git — all against real on-disk bytes. The
  // draft map is left intact; a version-conflict banner surfaces if a human edit
  // now races the agent's write (Slice 3's honest stale path).
  const onAgentFilesChanged = useCallback(
    (paths: string[]) => {
      for (const path of paths) {
        queryClient.invalidateQueries({
          queryKey: ["ide", "workspace", workspaceId, "document", path],
        });
      }
      queryClient.invalidateQueries({ queryKey: ["ide", "workspace", workspaceId, "tree"] });
      queryClient.invalidateQueries({ queryKey: ["ide", "workspace", workspaceId, "git"] });
    },
    [queryClient, workspaceId],
  );

  const openPath = useCallback((path: string, name?: string) => {
    setDiffOpen(false); // a file selection returns the editor area to the buffer
    setTabs((prev) =>
      prev.some((t) => t.path === path)
        ? prev
        : [...prev, { path, name: name ?? path.split("/").pop() ?? path }],
    );
    setActivePath(path);
  }, []);

  const openFile = useCallback(
    (node: FileNode) => openPath(node.path, node.name),
    [openPath],
  );

  const openDiff = useCallback((path: string | null) => {
    setDiffPath(path);
    setDiffOpen(true);
  }, []);

  const setDraft = useCallback((path: string, value: string | undefined) => {
    setDrafts((prev) => {
      if (value === undefined) {
        if (!(path in prev)) return prev;
        const rest = { ...prev };
        delete rest[path];
        return rest;
      }
      return { ...prev, [path]: value };
    });
  }, []);

  const clearDraft = useCallback(
    (path: string) => setDraft(path, undefined),
    [setDraft],
  );

  const closeTab = useCallback(
    (path: string) => {
      if (
        dirtyPaths.has(path) &&
        !window.confirm(`Discard unsaved changes to ${path}?`)
      ) {
        return;
      }
      setDraft(path, undefined);
      setTabs((prev) => {
        const next = prev.filter((t) => t.path !== path);
        setActivePath((cur) =>
          cur !== path ? cur : next.length ? next[next.length - 1].path : null,
        );
        return next;
      });
    },
    [dirtyPaths, setDraft],
  );

  if (isError && isSubsystemDisabled(error)) {
    return (
      <div className="wb-fullscreen-msg">
        <div>
          <AlertTriangle size={40} style={{ color: "var(--gold-500)", marginBottom: "1rem" }} />
          <h2 style={{ color: "var(--paper-100)", margin: "0 0 0.5rem" }}>IDE Subsystem Disabled</h2>
          <p style={{ maxWidth: 460, margin: "0 auto", lineHeight: 1.6 }}>
            The Agentic Development Environment is turned off. Set{" "}
            <code>ide.enabled: true</code> in <code>config/settings.yaml</code> and
            restart the ATLAS runtime.
          </p>
        </div>
      </div>
    );
  }

  if (isLoading) {
    return <div className="wb-fullscreen-msg">Loading workspace…</div>;
  }

  if (isError || !workspace) {
    return (
      <div className="wb-fullscreen-msg">
        <div>
          <AlertTriangle size={40} style={{ color: "var(--danger-400)", marginBottom: "1rem" }} />
          <h2 style={{ color: "var(--paper-100)", margin: "0 0 0.5rem" }}>Workspace not found</h2>
          <p style={{ maxWidth: 460, margin: "0 auto", lineHeight: 1.6 }}>
            No durable workspace matches <code>{workspaceId}</code>. It may have been
            closed. Open it again from the Command Center.
          </p>
        </div>
      </div>
    );
  }

  const ViewIcon = { explorer: Files, search: Search, scm: GitBranch, run: Play }[view];

  return (
    <div
      className="workbench"
      style={
        agentOpen
          ? undefined
          : { gridTemplateColumns: "48px 264px minmax(0, 1fr)" }
      }
    >
      <ActivityBar
        active={view}
        onSelect={setView}
        agentOpen={agentOpen}
        onToggleAgent={() => setAgentOpen((o) => !o)}
      />

      <aside className="wb-sidebar" aria-label={SIDEBAR_TITLE[view]}>
        <div className="wb-panel-head">
          <ViewIcon size={13} />
          {SIDEBAR_TITLE[view]}
        </div>
        <div className="wb-panel-body">
          {view === "explorer" ? (
            <Explorer
              workspaceId={workspaceId}
              activePath={activePath}
              onOpenFile={openFile}
            />
          ) : view === "scm" ? (
            <SourceControl
              workspaceId={workspaceId}
              activeDiffPath={diffOpen ? diffPath : undefined}
              onOpenDiff={openDiff}
            />
          ) : view === "run" ? (
            <RunPanel workspaceId={workspaceId} onOpenProcess={openProcess} />
          ) : (
            <p className="wb-placeholder">
              {SIDEBAR_TITLE[view]} — connected in a later slice.
            </p>
          )}
        </div>
      </aside>

      <div className="wb-main">
        <div className="wb-editor">
          {diffOpen ? (
            <DiffView
              key={diffPath ?? "__all__"}
              workspaceId={workspaceId}
              path={diffPath}
              onClose={() => setDiffOpen(false)}
            />
          ) : activePath ? (
            <>
              <EditorTabs
                tabs={tabs}
                activePath={activePath}
                dirtyPaths={dirtyPaths}
                onSelect={setActivePath}
                onClose={closeTab}
              />
              <CodeEditor
                key={activePath}
                workspaceId={workspaceId}
                path={activePath}
                draft={drafts[activePath]}
                onDraftChange={setDraft}
                onSaved={clearDraft}
              />
            </>
          ) : (
            <>
              <div className="wb-panel-head">Editor</div>
              <div className="wb-panel-body">
                <p className="wb-placeholder">
                  No file open. Select a file in the Explorer to view it.
                </p>
              </div>
            </>
          )}
        </div>
        <div className="wb-bottom">
          <div className="wb-panel-head" role="tablist" aria-label="Bottom panel">
            <button
              type="button"
              role="tab"
              aria-selected={bottomTab === "terminal"}
              className={bottomTab === "terminal" ? "wb-bottom-tab is-active" : "wb-bottom-tab"}
              onClick={() => setBottomTab("terminal")}
            >
              Terminal
            </button>
            {procView ? (
              <button
                type="button"
                role="tab"
                aria-selected={bottomTab === "process"}
                className={bottomTab === "process" ? "wb-bottom-tab is-active" : "wb-bottom-tab"}
                onClick={() => setBottomTab("process")}
                title={procView.command}
              >
                Process
              </button>
            ) : null}
            <button
              type="button"
              role="tab"
              aria-selected={bottomTab === "problems"}
              className={bottomTab === "problems" ? "wb-bottom-tab is-active" : "wb-bottom-tab"}
              onClick={() => setBottomTab("problems")}
            >
              Problems
            </button>
            <span className="wb-bottom-tab is-muted">Output</span>
            <button
              type="button"
              role="tab"
              aria-selected={bottomTab === "tests"}
              className={bottomTab === "tests" ? "wb-bottom-tab is-active" : "wb-bottom-tab"}
              onClick={() => setBottomTab("tests")}
            >
              Tests
            </button>
          </div>
          <div style={{ display: bottomTab === "terminal" ? "flex" : "none", flex: 1, minHeight: 0 }}>
            <TerminalView workspaceId={workspaceId} />
          </div>
          <div style={{ display: bottomTab === "problems" ? "flex" : "none", flex: 1, minHeight: 0 }}>
            <ProblemsView workspaceId={workspaceId} onOpenFile={(path) => openPath(path)} />
          </div>
          <div style={{ display: bottomTab === "tests" ? "flex" : "none", flex: 1, minHeight: 0 }}>
            <TestsView workspaceId={workspaceId} onOpenFile={(path) => openPath(path)} />
          </div>
          {procView && bottomTab === "process" ? (
            <ProcessOutputView
              key={procView.id}
              workspaceId={workspaceId}
              processId={procView.id}
              command={procView.command}
              onClose={() => {
                setProcView(null);
                setBottomTab("terminal");
              }}
            />
          ) : null}
        </div>
      </div>

      {agentOpen && (
        <aside className="wb-agent" aria-label="Agent panel">
          <AgentPanel
            workspaceId={workspaceId}
            onOpenFile={(path) => openPath(path)}
            onFilesChanged={onAgentFilesChanged}
          />
        </aside>
      )}

      <StatusBar workspace={workspace} git={git} />
    </div>
  );
}
