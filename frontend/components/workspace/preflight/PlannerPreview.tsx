import { Loader2, Zap, LayoutList, CheckCircle2, AlertTriangle, ShieldCheck } from "lucide-react";
import { useWorkspace } from "../WorkspaceState";

export function PlannerPreview({ 
  onConfirm, 
  onCancel, 
  isPending 
}: { 
  onConfirm: () => void; 
  onCancel: () => void;
  isPending: boolean;
}) {
  const { state } = useWorkspace();

  if (state.preflightStatus === "idle") return null;

  return (
    <div className="absolute inset-0 z-50 bg-ink-950/90 backdrop-blur-sm flex flex-col justify-center items-center rounded-lg p-6 animate-in fade-in zoom-in-95 duration-200">
      
      {state.preflightStatus === "analyzing" ? (
        <div className="flex flex-col items-center gap-4">
          <Loader2 className="w-8 h-8 text-gold-500 animate-spin" />
          <div className="text-slate-300 font-medium">Analyzing request & classifying safety...</div>
          <div className="text-xs text-slate-500 font-mono flex gap-2">
            <span>[Vision: OK]</span>
            <span>[Policy: SC-42]</span>
            <span>[Context: Build]</span>
          </div>
        </div>
      ) : (
        <div className="w-full max-w-lg bg-ink-900 border border-ink-700 rounded-lg overflow-hidden shadow-2xl">
          <div className="p-4 border-b border-ink-800 flex items-center justify-between bg-ink-950">
            <h3 className="font-medium text-slate-200 flex items-center gap-2">
              <Zap size={16} className="text-gold-500" />
              Task Pre-flight Summary
            </h3>
            <span className="text-xs font-mono px-2 py-1 bg-ink-800 rounded text-slate-400">
              DAG Preview
            </span>
          </div>

          <div className="p-4 space-y-4 text-sm">
            
            {/* Task Intent */}
            <div>
              <div className="text-xs text-slate-500 font-medium mb-1">INTENT</div>
              <p className="text-slate-300 italic">"{state.text.slice(0, 100)}{state.text.length > 100 ? '...' : ''}"</p>
            </div>

            {/* Routing & Safety */}
            <div className="grid grid-cols-2 gap-4">
              <div className="bg-ink-850 border border-ink-800 p-3 rounded">
                <div className="text-xs text-slate-500 font-medium mb-2 flex items-center gap-1.5">
                  <LayoutList size={14} /> Models Routed
                </div>
                <div className="flex flex-col gap-1 text-slate-300">
                  {state.attachments.some(a => a.type === 'image') && <div>• <span className="text-gold-400">GLM-4V</span> (Vision)</div>}
                  <div>• <span className="text-blue-400">GPT-5</span> (Reasoning)</div>
                </div>
              </div>
              <div className="bg-ink-850 border border-ink-800 p-3 rounded">
                <div className="text-xs text-slate-500 font-medium mb-2 flex items-center gap-1.5">
                  <ShieldCheck size={14} /> Safety Clearance
                </div>
                <div className="flex flex-col gap-1">
                  <div className="text-jade-400 flex items-center gap-1"><CheckCircle2 size={12}/> Policy OK</div>
                  <div className="text-amber-400 flex items-center gap-1"><AlertTriangle size={12}/> Needs Approval (Tool)</div>
                </div>
              </div>
            </div>

            {/* Execution Metrics */}
            <div className="flex items-center justify-between bg-ink-950 p-3 rounded border border-ink-800 text-xs">
              <div className="flex flex-col">
                <span className="text-slate-500 mb-1">Est. Runtime</span>
                <span className="text-slate-200 font-mono">15-45s</span>
              </div>
              <div className="flex flex-col">
                <span className="text-slate-500 mb-1">Context Size</span>
                <span className="text-slate-200 font-mono">~{(state.text.length/4).toFixed(0)} tks</span>
              </div>
              <div className="flex flex-col">
                <span className="text-slate-500 mb-1">Est. Cost</span>
                <span className="text-slate-200 font-mono">$0.002</span>
              </div>
            </div>
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
              {isPending ? "Executing..." : "Confirm & Execute"}
            </button>
          </div>
        </div>
      )}

    </div>
  );
}
