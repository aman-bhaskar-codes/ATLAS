/**
 * Example usage of WebSocket hooks
 * 
 * This file demonstrates how to use the WebSocket hooks in your components.
 * Copy these patterns into your actual components.
 */

import { useState, useEffect } from 'react';
import { useTaskEvents, useGlobalEvents, useEventBuffer } from './index';

// Example 1: Task monitoring component
export function TaskMonitor({ taskId }: { taskId: string }) {
  const { data: event, status, error } = useTaskEvents(taskId);
  
  return (
    <div className="p-4 border rounded">
      <div className="mb-2">
        Status: 
        <span className={
          status === 'connected' ? 'text-green-600' :
          status === 'connecting' ? 'text-yellow-600' :
          status === 'error' ? 'text-red-600' :
          'text-gray-600'
        }>
          {status}
        </span>
      </div>
      
      {error && (
        <div className="text-red-600 mb-2">
          Error: {error.message}
        </div>
      )}
      
      {event && (
        <div className="bg-gray-50 p-2 rounded">
          <div className="font-mono text-sm">
            {event.kind}
          </div>
          {event.metadata?.summary && (
            <div className="text-gray-600 text-sm mt-1">
              {event.metadata.summary}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// Example 2: Global event stream with stats
export function GlobalEventStream() {
  const { data: event, status } = useGlobalEvents({ debug: true });
  const [eventCount, setEventCount] = useState(0);
  const [eventTypes, setEventTypes] = useState<Record<string, number>>({});
  
  useEffect(() => {
    if (!event) return;
    
    setEventCount(prev => prev + 1);
    setEventTypes(prev => ({
      ...prev,
      [event.kind]: (prev[event.kind] || 0) + 1,
    }));
  }, [event]);
  
  return (
    <div className="p-4">
      <h2 className="text-xl font-bold mb-4">System Events</h2>
      
      <div className="grid grid-cols-2 gap-4 mb-4">
        <div className="bg-blue-50 p-4 rounded">
          <div className="text-sm text-gray-600">Total Events</div>
          <div className="text-2xl font-bold">{eventCount}</div>
        </div>
        
        <div className="bg-green-50 p-4 rounded">
          <div className="text-sm text-gray-600">Connection</div>
          <div className="text-2xl font-bold capitalize">{status}</div>
        </div>
      </div>
      
      {event && (
        <div className="bg-white border rounded p-4">
          <div className="text-xs text-gray-500 mb-1">
            Latest Event
          </div>
          <div className="font-mono text-sm">{event.kind}</div>
        </div>
      )}
    </div>
  );
}

// Example 3: Event history buffer
export function EventHistory({ taskId }: { taskId: string }) {
  const events = useEventBuffer(taskId, { maxSize: 50 });
  
  return (
    <div className="p-4">
      <h3 className="font-bold mb-2">Event History ({events.length})</h3>
      
      <div className="space-y-2 max-h-96 overflow-y-auto">
        {events.map((event, index) => (
          <div key={index} className="bg-gray-50 p-2 rounded text-sm">
            <div className="flex justify-between items-start">
              <span className="font-mono">{event.kind}</span>
              {event.historical && (
                <span className="text-xs text-gray-500 bg-gray-200 px-2 py-1 rounded">
                  REPLAY
                </span>
              )}
            </div>
            
            {event.metadata?.summary && (
              <div className="text-gray-600 text-xs mt-1">
                {event.metadata.summary}
              </div>
            )}
          </div>
        ))}
        
        {events.length === 0 && (
          <div className="text-gray-500 text-center py-4">
            No events yet
          </div>
        )}
      </div>
    </div>
  );
}
