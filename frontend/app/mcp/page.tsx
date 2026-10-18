"use client";
import { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { z } from 'zod';
import { requestContract, AtlasApiError } from '@/lib/api/client';
import { McpServerSchema, McpToolSchema, McpResourceSchema, McpPromptSchema } from '@/features/mcp/contracts';
import { ErrorRow } from '@/components/primitives/ErrorState';
import { EmptyState } from '@/components/primitives/EmptyState';
import { Button } from '@/components/primitives/Button';

function safeFailure(error: unknown): Error {
  if (error instanceof AtlasApiError) {
    if (error.status === 503) return new Error('MCP runtime is unavailable in this deployment.');
    if (error.status === 401 || error.status === 403) return new Error('Your current API credential does not authorize this MCP operation.');
    return new Error(`MCP request failed (${error.status}). Retry or inspect the server diagnostics locally.`);
  }
  return new Error('Could not read the MCP runtime. Check the connection and retry.');
}

export default function McpPage() {
  const servers = useQuery({ queryKey: ['mcp', 'servers'], queryFn: () => requestContract('/mcp/servers', z.array(McpServerSchema)) });
  const [selected, setSelected] = useState<string | null>(null);
  const server = servers.data?.find(row => row.server_id === selected);
  return <>
    <div className="crumb mb-6">ATLAS / <strong>MCP</strong></div>
    <section className="panel"><div className="section-head"><h1>MCP servers</h1><Button variant="secondary" isLoading={servers.isFetching} onClick={() => void servers.refetch()}>Refresh status</Button></div>
      <div className="research-history">
        <p className="platform-description">Inspect configured servers and their discovered capabilities. Connection changes require an owner credential.</p>
        {servers.isLoading && <p role="status">Loading MCP servers…</p>}
        {servers.isError && <ErrorRow error={safeFailure(servers.error)} onRetry={() => void servers.refetch()} />}
        {servers.data?.length === 0 && <EmptyState title="No MCP servers configured" description="Add a server through your local ATLAS configuration to inspect it here." />}
        <div className="component-shortcuts">{servers.data?.map(row => <Button key={row.server_id} variant={selected === row.server_id ? 'primary' : 'secondary'} aria-pressed={selected === row.server_id} onClick={() => setSelected(row.server_id)}>{row.name} · {row.state}</Button>)}</div>
      </div>
    </section>
    {server && <McpServerDetails key={server.server_id} server={server} />}
  </>;
}

function McpServerDetails({ server }: { server: z.infer<typeof McpServerSchema> }) {
  const client = useQueryClient();
  const base = `/mcp/servers/${encodeURIComponent(server.server_id)}`;
  const status = useQuery({ queryKey: ['mcp', server.server_id, 'status'], queryFn: () => requestContract(base, McpServerSchema) });
  const current = status.data ?? server;
  const tools = useQuery({ queryKey: ['mcp', server.server_id, 'tools'], queryFn: () => requestContract(`${base}/tools`, z.array(McpToolSchema)) });
  const resources = useQuery({ queryKey: ['mcp', server.server_id, 'resources'], queryFn: () => requestContract(`${base}/resources`, z.array(McpResourceSchema)), enabled: current.connected });
  const prompts = useQuery({ queryKey: ['mcp', server.server_id, 'prompts'], queryFn: () => requestContract(`${base}/prompts`, z.array(McpPromptSchema)), enabled: current.connected });
  const action = useMutation({
    mutationFn: (operation: 'connect' | 'disconnect' | 'refresh') => requestContract(`${base}/${operation}`, z.object({}), { method: 'POST' }),
    onSuccess: () => { void client.invalidateQueries({ queryKey: ['mcp'] }); },
  });
  return <section className="panel mt-6" aria-label="MCP server details"><div className="section-head"><h2>{current.name}</h2></div><div className="research-history">
    <p>{current.state} · {current.transport} · {current.enabled ? 'enabled' : 'disabled'} · {current.startup} startup · trust {current.trust_level}</p>
    <p>{current.tools} discovered tools{current.protocol_version ? ` · protocol ${current.protocol_version}` : ''}</p>
    {status.isError && <ErrorRow error={safeFailure(status.error)} onRetry={() => void status.refetch()} />}
    <div className="component-shortcuts">
      <Button variant="secondary" disabled={action.isPending || !current.enabled || current.connected} onClick={() => action.mutate('connect')}>Connect</Button>
      <Button variant="secondary" disabled={action.isPending || !current.connected} onClick={() => action.mutate('disconnect')}>Disconnect</Button>
      <Button variant="secondary" disabled={action.isPending || !current.connected} onClick={() => action.mutate('refresh')}>Refresh discovery</Button>
    </div>
    {action.isPending && <p role="status">Updating MCP connection…</p>}
    {action.isError && <ErrorRow error={safeFailure(action.error)} />}
    <h3>Tools</h3>{tools.isLoading && <p role="status">Loading tools…</p>}
    {tools.isError && <ErrorRow error={safeFailure(tools.error)} onRetry={() => void tools.refetch()} />}
    {tools.data?.length === 0 && <p>No tools discovered.</p>}
    <ul>{tools.data?.map(tool => <li key={tool.id}>{tool.name} · {tool.operations.join(', ')} · {tool.trust_level} · {tool.locality}</li>)}</ul>
    <h3>Resources</h3>
    {!current.connected && <p>Connect this server to inspect resources and prompts.</p>}
    {resources.isLoading && <p role="status">Loading resources…</p>}
    {resources.isError && <ErrorRow error={safeFailure(resources.error)} onRetry={() => void resources.refetch()} />}
    {resources.data?.length === 0 && <p>No resources discovered.</p>}
    <ul>{resources.data?.map((item, index) => <li key={index}>{item.name || 'Unnamed resource'}{item.mime_type ? ` · ${item.mime_type}` : ''}</li>)}</ul>
    <h3>Prompts</h3>{prompts.isLoading && <p role="status">Loading prompts…</p>}
    {prompts.isError && <ErrorRow error={safeFailure(prompts.error)} onRetry={() => void prompts.refetch()} />}
    {prompts.data?.length === 0 && <p>No prompts discovered.</p>}
    <ul>{prompts.data?.map((item, index) => <li key={index}>{item.name || 'Unnamed prompt'}</li>)}</ul>
  </div></section>;
}
