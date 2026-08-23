import { ArrowUpRight, Loader2, BrainCircuit, ShieldAlert, Cpu } from "lucide-react";
import { useWorkspace } from "./WorkspaceState";

export function CommandFooter({ onStart, isPending }: { onStart: () => void, isPending: boolean }) {
  const { state } = useWorkspace();
  const hasInput = state.text.trim().length > 0 || state.attachments.length > 0;

  return (
    <div className="flex items-center justify-between p-2 mt-2 border-t border-ink-800 bg-ink-900/50">
      
      {/* Left side: Quick actions / Indicators */}
      <div className="flex items-center gap-4 px-2">
        {/* Model Indicator */}
        <div className="flex items-center gap-1.5 text-xs text-slate-400" title="Selected Model">
          <BrainCircuit size={14} className={state.selectedModel === 'auto' ? 'text-gold-500' : 'text-slate-400'} />
          <span className="font-mono uppercase">{state.selectedModel}</span>
        </div>

        {/* Safety: a fixed statement, not a reading. This chip was bound to
            `state.safetyLevel`, which nothing ever dispatched — so its 'cleared'
            (green) and 'flagged' (red) branches were unreachable and it always
            displayed "Pending" while looking like a live classification. The
            classification genuinely happens server-side after submit, so that is
            what it now says. */}
        <div
          className="flex items-center gap-1.5 text-xs text-slate-500"
          title="ATLAS classifies this request against your safety policy after you start the task"
        >
          <ShieldAlert size={14} className="text-slate-500" />
          <span>Classified on start</span>
        </div>

        {/* Rough context size: chars/4 is a heuristic, hence the ~ and the title. */}
        <div className="flex items-center gap-1.5 text-xs text-slate-500" title="Rough context estimate (characters ÷ 4)">
          <Cpu size={14} />
          <span>~{(state.text.length / 4).toFixed(0)} tks</span>
        </div>
      </div>

      {/* Right side: Submit */}
      <button 
        className="primary flex items-center gap-2 px-4 py-1.5 text-sm font-medium"
        onClick={onStart} 
        disabled={isPending || !hasInput}
      >
        {isPending ? <Loader2 className="w-4 h-4 animate-spin" /> : <ArrowUpRight className="w-4 h-4" />}
        {isPending ? "Starting" : "Start task"}
      </button>

    </div>
  );
}
