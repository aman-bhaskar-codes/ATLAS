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

        {/* Safety Indicator */}
        <div className="flex items-center gap-1.5 text-xs text-slate-400" title="Safety classification">
          <ShieldAlert size={14} className={
            state.safetyLevel === 'cleared' ? 'text-jade-500' : 
            state.safetyLevel === 'flagged' ? 'text-ember-500' : 'text-slate-500'
          } />
          <span className="capitalize">{state.safetyLevel}</span>
        </div>

        {/* Est. Tokens (Placeholder) */}
        <div className="flex items-center gap-1.5 text-xs text-slate-500" title="Estimated Context Size">
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
