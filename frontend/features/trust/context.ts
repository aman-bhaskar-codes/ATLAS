interface Remote<T> { data?: T; isError?: boolean; isPending?: boolean; }
interface MemoryCounts { episode_count: number; fact_count: number; document_count: number; }

function observed<T>(label: string, query: Remote<T>, describe: (data: T) => string): string {
  if (query.isError) return `${label}: Unavailable`;
  if (query.isPending) return `${label}: Loading…`;
  if (query.data === undefined) return `${label}: Unavailable`;
  return `${label}: ${describe(query.data)}`;
}

export function contextLabels(
  draft: {text: string; attachmentCount: number},
  memory: Remote<MemoryCounts>,
  capabilities: Remote<{state: string}[]>,
  mcp: Remote<{connected: boolean}[]>,
) {
  const parts = [draft.text.trim() ? 'Request' : '', draft.attachmentCount ? `${draft.attachmentCount} attachment${draft.attachmentCount === 1 ? '' : 's'}` : ''].filter(Boolean);
  return {
    context: `Context: ${parts.join(' · ') || 'No request or attachments'}`,
    memory: observed('Memory', memory, data => `${data.episode_count} episodes · ${data.fact_count} facts · ${data.document_count} documents`),
    capabilities: observed('Capabilities', capabilities, data => `${data.filter(item => item.state === 'ready').length} ready / ${data.length} registered`),
    mcp: observed('MCP', mcp, data => `${data.filter(item => item.connected).length} connected / ${data.length} configured`),
  };
}
