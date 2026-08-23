/**
 * Event Visualization Components
 * 
 * Specialized components for rendering different event types with
 * appropriate formatting, syntax highlighting, and visual design.
 */

'use client';

import type { AtlasEvent } from '../../lib/websocket';

// Reasoning Event: Shows thought process and actions
export function ReasoningEventCard({ event }: { event: AtlasEvent }) {
  const thought = event.metadata?.thought;
  const action = event.metadata?.action;
  
  return (
    <div className="border-l-4 border-cyan-400 bg-cyan-50 p-4 rounded-r">
      <div className="flex items-center gap-2 mb-2">
        <span className="text-2xl">💭</span>
        <span className="font-semibold text-cyan-900">Reasoning</span>
        <span className="text-xs bg-cyan-200 text-cyan-800 px-2 py-1 rounded">
          {event.kind}
        </span>
      </div>
      
      {thought && (
        <div className="mb-3">
          <div className="text-xs text-cyan-700 font-medium mb-1">Thought</div>
          <div className="bg-white rounded p-3 text-sm text-gray-800 italic">
            &quot;{thought}&quot;
          </div>
        </div>
      )}
      
      {action && (
        <div>
          <div className="text-xs text-cyan-700 font-medium mb-1">Action</div>
          <div className="bg-cyan-100 rounded p-3 text-sm font-mono text-cyan-900">
            {action}
          </div>
        </div>
      )}
    </div>
  );
}

// Tool Event: Shows command execution with results
export function ToolEventCard({ event }: { event: AtlasEvent }) {
  const tool = event.metadata?.tool;
  const operation = event.metadata?.operation;
  const args = event.metadata?.args;
  const result = event.metadata?.result;
  const error = event.metadata?.error;
  const isSuccess = event.kind.includes('completed');
  
  return (
    <div className={`border-l-4 ${
      isSuccess ? 'border-green-400 bg-green-50' : 'border-yellow-400 bg-yellow-50'
    } p-4 rounded-r`}>
      <div className="flex items-center gap-2 mb-2">
        <span className="text-2xl">⚙️</span>
        <span className="font-semibold">Tool Execution</span>
        <span className={`text-xs px-2 py-1 rounded ${
          isSuccess ? 'bg-green-200 text-green-800' : 'bg-yellow-200 text-yellow-800'
        }`}>
          {event.kind}
        </span>
      </div>
      
      {tool && (
        <div className="mb-2">
          <span className="text-xs text-gray-600">Tool:</span>
          <span className="ml-2 font-mono text-sm font-medium">{tool}</span>
          {operation && (
            <span className="ml-1 text-xs text-gray-500">.{operation}</span>
          )}
        </div>
      )}
      
      {args && (
        <div className="mb-3">
          <div className="text-xs text-gray-600 mb-1">Arguments</div>
          <pre className="bg-gray-800 text-green-400 rounded p-3 text-xs overflow-x-auto">
            {JSON.stringify(args, null, 2)}
          </pre>
        </div>
      )}
      
      {result && (
        <div className="mb-3">
          <div className="text-xs text-gray-600 mb-1">Result</div>
          <div className="bg-white border rounded p-3 text-sm">
            {typeof result === 'string' ? result : JSON.stringify(result, null, 2)}
          </div>
        </div>
      )}
      
      {error && (
        <div className="bg-red-50 border border-red-200 rounded p-3 text-sm text-red-800">
          <div className="font-medium mb-1">Error</div>
          {error}
        </div>
      )}
    </div>
  );
}

// Safety Event: Shows security tier and approval decisions
export function SafetyEventCard({ event }: { event: AtlasEvent }) {
  const tier = event.metadata?.tier;
  const reason = event.metadata?.reason;
  const decision = event.metadata?.decision;
  const requiresApproval = event.metadata?.requires_approval;
  
  const getTierColor = (tier?: number) => {
    if (!tier) return 'bg-gray-100 text-gray-800';
    if (tier >= 3) return 'bg-red-100 text-red-800';
    if (tier >= 2) return 'bg-yellow-100 text-yellow-800';
    return 'bg-green-100 text-green-800';
  };
  
  return (
    <div className="border-l-4 border-purple-400 bg-purple-50 p-4 rounded-r">
      <div className="flex items-center gap-2 mb-2">
        <span className="text-2xl">🛡️</span>
        <span className="font-semibold text-purple-900">Safety Check</span>
        <span className="text-xs bg-purple-200 text-purple-800 px-2 py-1 rounded">
          {event.kind}
        </span>
      </div>
      
      {tier !== undefined && (
        <div className="mb-2">
          <span className="text-xs text-gray-600">Risk Tier:</span>
          <span className={`ml-2 px-2 py-1 rounded text-sm font-bold ${getTierColor(tier)}`}>
            Tier {tier}
          </span>
          {requiresApproval && (
            <span className="ml-2 text-xs bg-yellow-100 text-yellow-800 px-2 py-1 rounded">
              Requires Approval
            </span>
          )}
        </div>
      )}
      
      {reason && (
        <div className="bg-white rounded p-3 text-sm text-gray-800">
          {reason}
        </div>
      )}
      
      {decision && (
        <div className={`mt-2 rounded p-2 text-sm font-medium ${
          decision === 'approved' ? 'bg-green-100 text-green-800' :
          decision === 'denied' ? 'bg-red-100 text-red-800' :
          'bg-gray-100 text-gray-800'
        }`}>
          Decision: {decision}
        </div>
      )}
    </div>
  );
}

// Memory Event: Shows information retrieval
export function MemoryEventCard({ event }: { event: AtlasEvent }) {
  const query = event.metadata?.query;
  const results = event.metadata?.results;
  const count = event.metadata?.count || 0;
  
  return (
    <div className="border-l-4 border-indigo-400 bg-indigo-50 p-4 rounded-r">
      <div className="flex items-center gap-2 mb-2">
        <span className="text-2xl">📚</span>
        <span className="font-semibold text-indigo-900">Memory Retrieval</span>
        <span className="text-xs bg-indigo-200 text-indigo-800 px-2 py-1 rounded">
          {event.kind}
        </span>
      </div>
      
      {query && (
        <div className="mb-3">
          <div className="text-xs text-indigo-700 font-medium mb-1">Query</div>
          <div className="bg-white rounded p-3 text-sm text-gray-800">
            {query}
          </div>
        </div>
      )}
      
      {count > 0 && (
        <div className="text-xs text-indigo-700 mb-2">
          Found {count} result{count !== 1 ? 's' : ''}
        </div>
      )}
      
      {results && Array.isArray(results) && results.length > 0 && (
        <div className="space-y-2">
          {results.slice(0, 3).map((result: unknown, i: number) => (
            <div key={i} className="bg-white border rounded p-2 text-sm">
              {typeof result === 'string' ? result : JSON.stringify(result)}
            </div>
          ))}
          {results.length > 3 && (
            <div className="text-xs text-center text-indigo-600">
              ...and {results.length - 3} more
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// Generic Event Card with smart routing to specialized components
export function SmartEventCard({ event }: { event: AtlasEvent }) {
  const kind = event.kind.toLowerCase();
  
  // Route to specialized component based on event type
  if (kind.includes('thought') || kind.includes('action') || kind.includes('reasoning')) {
    return <ReasoningEventCard event={event} />;
  }
  
  if (kind.includes('tool')) {
    return <ToolEventCard event={event} />;
  }
  
  if (kind.includes('safety') || kind.includes('tier') || kind.includes('approval')) {
    return <SafetyEventCard event={event} />;
  }
  
  if (kind.includes('memory') || kind.includes('retrieved')) {
    return <MemoryEventCard event={event} />;
  }
  
  // Default: simple card for other event types
  return (
    <div className="border-l-4 border-gray-400 bg-gray-50 p-4 rounded-r">
      <div className="flex items-center gap-2 mb-2">
        <span className="text-2xl">📋</span>
        <span className="font-semibold">{event.kind}</span>
      </div>
      
      {event.metadata?.summary && (
        <div className="text-sm text-gray-700">
          {event.metadata.summary}
        </div>
      )}
      
      {event.metadata && Object.keys(event.metadata).length > 1 && (
        <details className="mt-2">
          <summary className="text-xs text-blue-600 cursor-pointer hover:text-blue-800">
            Show details
          </summary>
          <pre className="mt-2 bg-gray-100 rounded p-2 text-xs overflow-x-auto">
            {JSON.stringify(event.metadata, null, 2)}
          </pre>
        </details>
      )}
    </div>
  );
}
