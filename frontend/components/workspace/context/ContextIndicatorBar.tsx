import { Database, LayoutTemplate, Link2 } from "lucide-react";

export function ContextIndicatorBar() {
  return (
    <div className="flex items-center gap-3 px-4 py-2 border-b border-ink-800 bg-ink-900/30">
      <div className="flex items-center gap-1.5 text-[11px] text-slate-400">
        <LayoutTemplate size={12} className="text-gold-500/70" />
        <span className="font-medium tracking-wide">Workspace: ATLAS Default</span>
      </div>
      <div className="flex items-center gap-1.5 text-[11px] text-slate-400">
        <Database size={12} className="text-blue-400/70" />
        <span className="font-medium tracking-wide">Memory: Active</span>
      </div>
      <div className="flex items-center gap-1.5 text-[11px] text-slate-400">
        <Link2 size={12} className="text-jade-400/70" />
        <span className="font-medium tracking-wide">MCP: Chrome, Files</span>
      </div>
    </div>
  );
}
