"use client";
// frontend/components/command/CommandPalette.tsx
//
// Global ⌘K / Ctrl-K command palette. Zero Dead UI: every command either
// navigates to a route that actually renders (verified against app/**/page.tsx)
// or performs a real backend action (create a task via atlasApi.createTask).
// No fake interactions.
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { atlasApi } from "@/lib/api/client";
import { generateIdempotencyKey } from "@/features/trust/idempotency";
import { Search, CornerDownLeft, Loader2, ArrowRight } from "lucide-react";

interface NavCommand {
  id: string;
  label: string;
  hint: string;
  keywords: string;
  href: string;
}

// Only routes that actually render today. Do not add links to unbuilt sections.
const NAV_COMMANDS: NavCommand[] = [
  { id: "home", label: "Go to Home", hint: "Command Center", keywords: "home command center start", href: "/" },
  { id: "tasks", label: "Go to Tasks", hint: "All runs", keywords: "tasks runs history list", href: "/tasks" },
  { id: "live", label: "Go to Live Run", hint: "Active runtime", keywords: "live run active streaming trace", href: "/tasks/live" },
  { id: "approvals", label: "Go to Approvals", hint: "Pending decisions", keywords: "approvals approve deny pending trust", href: "/approvals" },
  { id: "activity", label: "Go to Activity", hint: "Event search", keywords: "activity events search stream feed", href: "/events/search" },
  { id: "audit", label: "Go to Audit", hint: "Decision log", keywords: "audit log chain security verify", href: "/audit" },
  { id: "memory", label: "Go to Memory", hint: "Facts & recall", keywords: "memory facts recall", href: "/memory" },
  { id: "capabilities", label: "Go to Capabilities", hint: "Tool posture", keywords: "capabilities tools posture", href: "/capabilities" },
  { id: "automations", label: "Go to Automations", hint: "Triggers", keywords: "automations triggers autonomy schedule", href: "/automations" },
  { id: "providers", label: "Go to Providers", hint: "Models & health", keywords: "providers models health", href: "/providers" },
  { id: "cost", label: "Go to Cost", hint: "Spend & quota", keywords: "cost spend budget quota", href: "/cost" },
  { id: "settings", label: "Go to Settings", hint: "Configuration", keywords: "settings config profile", href: "/settings" },
];

interface PaletteItem {
  key: string;
  kind: "create" | "nav";
  label: string;
  hint: string;
  run: () => void | Promise<void>;
}

export function CommandPalette() {
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [activeIndex, setActiveIndex] = useState(0);
  const [creating, setCreating] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  const close = useCallback(() => {
    setOpen(false);
    setQuery("");
    setActiveIndex(0);
    setError(null);
    setCreating(false);
  }, []);

  // Global hotkey (⌘K / Ctrl-K) + external open event from the Topbar launcher.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setOpen((o) => !o);
      } else if (e.key === "Escape") {
        setOpen(false);
      }
    };
    const onOpen = () => setOpen(true);
    window.addEventListener("keydown", onKey);
    window.addEventListener("atlas:open-command-palette", onOpen);
    return () => {
      window.removeEventListener("keydown", onKey);
      window.removeEventListener("atlas:open-command-palette", onOpen);
    };
  }, []);

  useEffect(() => {
    if (!open) return;
    const t = setTimeout(() => inputRef.current?.focus(), 0);
    return () => clearTimeout(t);
  }, [open]);

  const q = query.trim().toLowerCase();
  const filteredNav = useMemo(
    () =>
      q
        ? NAV_COMMANDS.filter((c) => `${c.label} ${c.keywords}`.toLowerCase().includes(q))
        : NAV_COMMANDS,
    [q],
  );

  const trimmed = query.trim();
  const items: PaletteItem[] = useMemo(() => {
    const list: PaletteItem[] = [];
    if (trimmed.length > 0) {
      list.push({
        key: "create",
        kind: "create",
        label: `Create task: “${trimmed}”`,
        hint: "Enter to run",
        run: async () => {
          setCreating(true);
          setError(null);
          try {
            const task = await atlasApi.createTask({
              request: trimmed,
              idempotency_key: generateIdempotencyKey(),
            });
            close();
            router.push(`/tasks/${task.id}`);
          } catch (err) {
            setCreating(false);
            setError(err instanceof Error ? err.message : "Failed to create task.");
          }
        },
      });
    }
    for (const c of filteredNav) {
      list.push({
        key: c.id,
        kind: "nav",
        label: c.label,
        hint: c.hint,
        run: () => {
          close();
          router.push(c.href);
        },
      });
    }
    return list;
  }, [trimmed, filteredNav, close, router]);

  // Keep the active index inside the (changing) list bounds without a state
  // write in an effect — derive a clamped value at render time.
  const clampedIndex = items.length ? Math.min(activeIndex, items.length - 1) : 0;

  if (!open) return null;

  const onListKey = (e: React.KeyboardEvent) => {
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setActiveIndex(Math.min(clampedIndex + 1, items.length - 1));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setActiveIndex(Math.max(clampedIndex - 1, 0));
    } else if (e.key === "Enter") {
      e.preventDefault();
      void items[clampedIndex]?.run();
    }
  };

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label="Command palette"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) close();
      }}
      style={{
        position: "fixed",
        inset: 0,
        zIndex: 100,
        background: "oklch(10% 0.02 278 / 0.55)",
        backdropFilter: "blur(2px)",
        display: "flex",
        alignItems: "flex-start",
        justifyContent: "center",
        paddingTop: "12vh",
      }}
    >
      <div
        className="bg-[var(--color-ink-900)] border border-[var(--color-line)] rounded-[var(--radius-md)] shadow-lg"
        style={{ width: "min(640px, 92vw)", overflow: "hidden" }}
        onKeyDown={onListKey}
      >
        <div className="flex items-center gap-2 px-3 border-b border-[var(--color-line)]">
          <Search className="w-4 h-4 text-[var(--color-paper-500)]" />
          <input
            ref={inputRef}
            value={query}
            onChange={(e) => {
              setQuery(e.target.value);
              setActiveIndex(0);
            }}
            placeholder="Search commands, or type a task for ATLAS…"
            className="flex-1 bg-transparent py-3 text-sm text-[var(--color-paper-100)] placeholder:text-[var(--color-paper-500)] focus:outline-none"
          />
          <kbd className="text-[0.65rem] text-[var(--color-paper-500)] border border-[var(--color-line)] rounded px-1.5 py-0.5">
            ESC
          </kbd>
        </div>

        {error && (
          <div className="px-3 py-2 text-xs text-[var(--color-danger-400)] border-b border-[var(--color-line)]">
            {error}
          </div>
        )}

        <ul style={{ maxHeight: "min(420px, 60vh)", overflowY: "auto" }} className="py-1">
          {items.length === 0 ? (
            <li className="px-3 py-6 text-center text-sm text-[var(--color-paper-500)]">
              No matching commands.
            </li>
          ) : (
            items.map((item, idx) => (
              <li key={item.key}>
                <button
                  type="button"
                  onMouseEnter={() => setActiveIndex(idx)}
                  onClick={() => void item.run()}
                  disabled={item.kind === "create" && creating}
                  className="w-full flex items-center justify-between gap-3 px-3 py-2 text-left text-sm disabled:opacity-60"
                  style={{
                    background: idx === clampedIndex ? "var(--color-ink-800)" : "transparent",
                    color: "var(--color-paper-100)",
                  }}
                >
                  <span className="flex items-center gap-2 truncate">
                    {item.kind === "create" ? (
                      creating ? (
                        <Loader2 className="w-3.5 h-3.5 animate-spin shrink-0" />
                      ) : (
                        <ArrowRight className="w-3.5 h-3.5 shrink-0 text-[var(--color-royal-400)]" />
                      )
                    ) : null}
                    <span className="truncate">{item.label}</span>
                  </span>
                  <span className="flex items-center gap-1 text-[0.7rem] text-[var(--color-paper-500)] shrink-0">
                    {idx === clampedIndex ? <CornerDownLeft className="w-3 h-3" /> : null}
                    {item.hint}
                  </span>
                </button>
              </li>
            ))
          )}
        </ul>
      </div>
    </div>
  );
}
