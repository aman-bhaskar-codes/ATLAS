import { Database, LayoutTemplate, Link2, Wrench } from "lucide-react";
import { useQuery } from '@tanstack/react-query';
import { z } from 'zod';
import { atlasApi, requestContract } from '@/lib/api/client';
import { useMemoryStats } from '@/features/memory/queries';
import { contextLabels } from '@/features/trust/context';
import { useWorkspace } from '../WorkspaceState';

const MCPServersSchema = z.array(z.object({
  server_id: z.string(), name: z.string(), state: z.string(), connected: z.boolean(),
}));

export function ContextIndicatorBar() {
  const {state} = useWorkspace();
  const memory = useMemoryStats();
  const capabilities = useQuery({queryKey: ['capabilities'], queryFn: atlasApi.capabilities, refetchInterval: 10_000});
  const mcp = useQuery({
    queryKey: ['mcp', 'servers'], queryFn: () => requestContract('/mcp/servers', MCPServersSchema), refetchInterval: 10_000,
  });
  const labels = contextLabels({text: state.text, attachmentCount: state.attachments.length}, memory, capabilities, mcp);
  return (
    <div aria-label="Command context" className="flex flex-wrap items-center gap-3 px-4 py-2 border-b border-ink-800 bg-ink-900/30">
      <div className="flex items-center gap-1.5 text-[11px] text-slate-400">
        <LayoutTemplate size={12} className="text-gold-500/70" />
        <span className="font-medium tracking-wide" title="No workspace is selected in this composer.">{labels.context}</span>
      </div>
      <div className="flex items-center gap-1.5 text-[11px] text-slate-400">
        <Database size={12} className="text-blue-400/70" />
        <span className="font-medium tracking-wide">{labels.memory}</span>
      </div>
      <div className="flex items-center gap-1.5 text-[11px] text-slate-400">
        <Wrench size={12} className="text-gold-500/70" />
        <span className="font-medium tracking-wide">{labels.capabilities}</span>
      </div>
      <div className="flex items-center gap-1.5 text-[11px] text-slate-400">
        <Link2 size={12} className="text-jade-400/70" />
        <span className="font-medium tracking-wide">{labels.mcp}</span>
      </div>
    </div>
  );
}
