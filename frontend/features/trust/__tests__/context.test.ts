import { describe, expect, it } from 'vitest';
import { contextLabels } from '../context';

describe('workspace context truth', () => {
  it('distinguishes unavailable/loading from connected services', () => {
    expect(contextLabels({text: '', attachmentCount: 0}, {isPending: true}, {isError: true}, {isPending: true})).toEqual({
      context: 'Context: No request or attachments', memory: 'Memory: Loading…',
      capabilities: 'Capabilities: Unavailable', mcp: 'MCP: Loading…',
    });
  });
  it('reports selected draft and observed counts without inventing selected workspace or active memory', () => {
    const labels = contextLabels({text: 'request', attachmentCount: 2},
      {data: {episode_count: 0, fact_count: 0, document_count: 0}},
      {data: [{state: 'ready'}, {state: 'planned'}, {state: 'unavailable'}]},
      {data: [{connected: true}, {connected: false}]},
    );
    expect(labels.context).toBe('Context: Request · 2 attachments');
    expect(labels.memory).toBe('Memory: 0 episodes · 0 facts · 0 documents');
    expect(labels.capabilities).toBe('Capabilities: 1 ready / 3 registered');
    expect(labels.mcp).toBe('MCP: 1 connected / 2 configured');
    expect(JSON.stringify(labels)).not.toContain('Active');
  });
  it('does not present stale cache as healthy after query failure', () => {
    const labels = contextLabels({text: ' ', attachmentCount: 0},
      {data: {episode_count: 8, fact_count: 2, document_count: 1}, isError: true},
      {data: []}, {data: []},
    );
    expect(labels.memory).toBe('Memory: Unavailable');
    expect(labels.mcp).toBe('MCP: 0 connected / 0 configured');
  });
});
