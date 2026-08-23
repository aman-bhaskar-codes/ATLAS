import { Loader2, Zap, Paperclip, ShieldQuestion } from "lucide-react";
import { useWorkspace } from "../WorkspaceState";
import { ErrorRow } from "@/components/primitives/ErrorState";

/**
 * The confirmation gate shown between "Start task" and the actual POST /tasks.
 *
 * WHAT WAS REMOVED, and why it mattered more here than anywhere else in the UI:
 * this panel used to render a **safety clearance it had not computed** — a green
 * "✓ Policy OK" and an amber "⚠ Needs Approval (Tool)" — on the exact screen where
 * the user authorises execution. It also invented "Models Routed: GPT-5 (Reasoning)
 * / GLM-4V (Vision)", "Est. Runtime 15-45s", "Est. Cost $0.002", a "DAG Preview"
 * badge with no DAG, and a `[Vision: OK] [Policy: SC-42] [Context: Build]` diagnostic
 * strip. None of it came from the backend: there is no pre-flight endpoint, the
 * safety engine classifies during orchestration (i.e. after this POST), and the
 * model registry is at /ops/models, which this component never called.
 *
 * A fabricated clearance on an authorisation gate is the worst version of the
 * problem — it invites the user to consent on the strength of a check that never
 * ran. So everything here is now either read from `state` (the user's own input) or
 * a statement about how ATLAS behaves, and the panel says plainly that classification
 * has not happened yet.
 */
export function PlannerPreview({
  onConfirm,
  onCancel,
  isPending,
  error,
}: {
  onConfirm: () => void;
  onCancel: () => void;
  isPending: boolean;
  /** The submit failure. Previously nothing rendered it — see CommandWorkspace. */
  error?: unknown;
}) {
  const { state } = useWorkspace();

  if (state.preflightStatus === "idle") return null;

  return (
    <div className="absolute inset-0 z-50 bg-ink-950/90 backdrop-blur-sm flex flex-col justify-center items-center rounded-lg p-6 animate-in fade-in zoom-in-95 duration-200">
      <div className="w-full max-w-lg bg-ink-900 border border-ink-700 rounded-lg overflow-hidden shadow-2xl">
        <div className="p-4 border-b border-ink-800 flex items-center justify-between bg-ink-950">
          <h3 className="font-medium text-slate-200 flex items-center gap-2">
            <Zap size={16} className="text-gold-500" />
            Start this task?
          </h3>
        </div>

        <div className="p-4 space-y-4 text-sm">
          {/* The request itself — the only content on this screen that was ever real. */}
          <div>
            <div className="text-xs text-slate-500 font-medium mb-1">REQUEST</div>
            <p className="text-slate-300 italic">
              &quot;{state.text.slice(0, 200)}
              {state.text.length > 200 ? "…" : ""}&quot;
            </p>
          </div>

          {state.attachments.length > 0 && (
            <div>
              <div className="text-xs text-slate-500 font-medium mb-1 flex items-center gap-1.5">
                <Paperclip size={13} /> ATTACHMENTS
              </div>
              <ul className="text-slate-300 flex flex-col gap-0.5 m-0 p-0 list-none">
                {state.attachments.map((att) => (
                  <li key={att.id} className="font-mono text-xs">
                    {att.file.name} <span className="text-slate-500">· {att.type}</span>
                  </li>
                ))}
              </ul>
            </div>
          )}

          {/* Replaces the fake clearance. This describes what ATLAS does, which is
              checkable in the code; it does not claim a verdict for this request. */}
          <div className="bg-ink-850 border border-ink-800 p-3 rounded flex gap-2.5">
            <ShieldQuestion size={15} className="text-slate-500 shrink-0 mt-0.5" />
            <div className="text-xs text-slate-400 leading-relaxed">
              Model routing and safety classification are decided by ATLAS after you
              start the task — they are not previewed here. If the safety engine
              rules the plan needs your approval, execution stops and waits for it.
            </div>
          </div>

          <div className="flex items-center gap-6 bg-ink-950 p-3 rounded border border-ink-800 text-xs">
            <div className="flex flex-col">
              <span className="text-slate-500 mb-1">Request length</span>
              <span className="text-slate-200 font-mono">{state.text.length} chars</span>
            </div>
            <div className="flex flex-col">
              <span className="text-slate-500 mb-1">Model selection</span>
              <span className="text-slate-200 font-mono">{state.selectedModel}</span>
            </div>
          </div>

          {/* A rejected POST /tasks used to be completely silent: the button reset,
              this panel stayed open, and a 403 from the safety engine looked
              identical to a click that did nothing. */}
          {error !== undefined && error !== null && <ErrorRow error={error} />}
        </div>

        <div className="p-4 border-t border-ink-800 flex justify-end gap-3 bg-ink-950">
          <button
            onClick={onCancel}
            disabled={isPending}
            className="px-4 py-2 text-sm text-slate-400 hover:text-slate-200"
          >
            Cancel
          </button>
          <button
            onClick={onConfirm}
            disabled={isPending}
            className="primary px-4 py-2 text-sm flex items-center gap-2"
          >
            {isPending && <Loader2 className="w-4 h-4 animate-spin" />}
            {isPending ? "Starting…" : "Start task"}
          </button>
        </div>
      </div>
    </div>
  );
}
