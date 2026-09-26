"use client";

import { X } from "lucide-react";

export interface OpenTab {
  /** Workspace-relative path — the tab identity and the document query key. */
  path: string;
  /** Basename shown on the tab. */
  name: string;
}

/**
 * Editor tab strip (spec §4 Editor Workbench). Purely presentational: it reflects
 * the open-tab set the {@link Workbench} owns and reports selection/close intents
 * back up. No data fetching happens here.
 */
export function EditorTabs({
  tabs,
  activePath,
  dirtyPaths,
  onSelect,
  onClose,
}: {
  tabs: OpenTab[];
  activePath: string | null;
  dirtyPaths: ReadonlySet<string>;
  onSelect: (path: string) => void;
  onClose: (path: string) => void;
}) {
  if (tabs.length === 0) return null;
  return (
    <div className="wb-tabs" role="tablist" aria-label="Open editors">
      {tabs.map((tab) => {
        const dirty = dirtyPaths.has(tab.path);
        return (
          <div
            key={tab.path}
            className="wb-tab"
            role="tab"
            aria-selected={tab.path === activePath}
            onClick={() => onSelect(tab.path)}
            title={tab.path}
          >
            <span>{tab.name}</span>
            {dirty && <span className="wb-tab-dirty" aria-label="Unsaved changes">●</span>}
            <span
              className="wb-tab-close"
              role="button"
              aria-label={`Close ${tab.name}`}
              onClick={(e) => {
                e.stopPropagation();
                onClose(tab.path);
              }}
            >
              <X size={12} />
            </span>
          </div>
        );
      })}
    </div>
  );
}
