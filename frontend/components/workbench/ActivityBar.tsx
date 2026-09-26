"use client";

import { Files, Search, GitBranch, Play, Bot } from "lucide-react";

/** The primary-sidebar views the activity bar switches between (spec §4). */
export type ActivityView = "explorer" | "search" | "scm" | "run";

const ITEMS: { view: ActivityView; label: string; Icon: typeof Files }[] = [
  { view: "explorer", label: "Explorer", Icon: Files },
  { view: "search", label: "Search", Icon: Search },
  { view: "scm", label: "Source Control", Icon: GitBranch },
  { view: "run", label: "Run", Icon: Play },
];

/**
 * The far-left activity bar. Switches the primary sidebar view and toggles the
 * agent panel. Slice-1: the switching is wired (real state); the panels those
 * views reveal are filled by later slices.
 */
export function ActivityBar({
  active,
  onSelect,
  agentOpen,
  onToggleAgent,
}: {
  active: ActivityView;
  onSelect: (view: ActivityView) => void;
  agentOpen: boolean;
  onToggleAgent: () => void;
}) {
  return (
    <nav className="wb-activity" aria-label="Workbench views">
      {ITEMS.map(({ view, label, Icon }) => (
        <button
          key={view}
          type="button"
          aria-label={label}
          title={label}
          aria-pressed={active === view}
          onClick={() => onSelect(view)}
        >
          <Icon size={20} />
        </button>
      ))}
      <span style={{ flex: 1 }} />
      <button
        type="button"
        aria-label="Agent panel"
        title="Agent panel"
        aria-pressed={agentOpen}
        onClick={onToggleAgent}
      >
        <Bot size={20} />
      </button>
    </nav>
  );
}
