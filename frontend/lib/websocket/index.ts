/**
 * Convenience hooks for ATLAS event streams.
 * 
 * useTaskEvents: Subscribe to events for a specific task
 * useGlobalEvents: Subscribe to all system events
 */

import { useMemo, useState, useEffect } from 'react';
import { useWebSocket, type WebSocketOptions, type UseWebSocketReturn } from './useWebSocket';

export interface AtlasEvent {
  correlation_id: string;
  task_id?: string;
  kind: string;
  state?: string;
  metadata?: Record<string, any>;
  _timestamp?: string;
  _topic?: string;
  historical?: boolean;
}

/**
 * Subscribe to events for a specific task with historical replay.
 * 
 * @example
 * ```tsx
 * function TaskMonitor({ taskId }: { taskId: string }) {
 *   const { data: event, status } = useTaskEvents(taskId);
 *   
 *   return (
 *     <div>
 *       <div>Status: {status}</div>
 *       {event && <EventCard event={event} />}
 *     </div>
 *   );
 * }
 * ```
 */
export function useTaskEvents(
  taskId: string | null,
  options: WebSocketOptions = {}
): UseWebSocketReturn<AtlasEvent> {
  const url = useMemo(() => {
    if (!taskId) return null;
    
    const baseUrl = process.env.NEXT_PUBLIC_ATLAS_WS_URL || 'ws://localhost:8000';
    return `${baseUrl}/ws/tasks/${encodeURIComponent(taskId)}/stream`;
  }, [taskId]);
  
  return useWebSocket<AtlasEvent>(url, {
    autoReconnect: true,
    ...options,
  });
}

/**
 * Subscribe to all system events (global firehose).
 * 
 * @example
 * ```tsx
 * function Dashboard() {
 *   const { data: event, status } = useGlobalEvents();
 *   
 *   return (
 *     <div>
 *       <ConnectionStatus status={status} />
 *       {event && <div>Latest: {event.kind}</div>}
 *     </div>
 *   );
 * }
 * ```
 */
export function useGlobalEvents(
  options: WebSocketOptions = {}
): UseWebSocketReturn<AtlasEvent> {
  const url = useMemo(() => {
    const baseUrl = process.env.NEXT_PUBLIC_ATLAS_WS_URL || 'ws://localhost:8000';
    return `${baseUrl}/ws/events`;
  }, []);
  
  return useWebSocket<AtlasEvent>(url, {
    autoReconnect: true,
    ...options,
  });
}

/**
 * Hook to accumulate events into a buffer (useful for displaying event history).
 * 
 * @example
 * ```tsx
 * function EventLog({ taskId }: { taskId: string }) {
 *   const events = useEventBuffer(taskId, { maxSize: 100 });
 *   
 *   return (
 *     <ul>
 *       {events.map((event, i) => (
 *         <li key={i}>{event.kind}</li>
 *       ))}
 *     </ul>
 *   );
 * }
 * ```
 */
export function useEventBuffer(
  taskId: string | null,
  options: { maxSize?: number } = {}
): AtlasEvent[] {
  const { maxSize = 1000 } = options;
  const { data: event } = useTaskEvents(taskId);
  const [buffer, setBuffer] = useState<AtlasEvent[]>([]);
  
  useEffect(() => {
    if (!event) return;
    
    setBuffer((prev) => {
      const next = [...prev, event];
      if (next.length > maxSize) {
        next.shift(); // Remove oldest
      }
      return next;
    });
  }, [event, maxSize]);
  
  return buffer;
}

// Re-export for convenience
export { useWebSocket } from './useWebSocket';
export type { ConnectionStatus, WebSocketOptions, UseWebSocketReturn } from './useWebSocket';
