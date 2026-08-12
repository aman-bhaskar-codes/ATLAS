/**
 * Example usage of EventFeed component with live WebSocket events
 */

'use client';

import { useGlobalEvents, useEventBuffer } from '../../lib/websocket';
import { EventFeed } from './EventFeed';

/**
 * Global event stream example - shows all system events
 */
export function GlobalEventFeedExample() {
  const { data: latestEvent, status } = useGlobalEvents();
  const allEvents = useEventBuffer(null, { maxSize: 500 });
  
  return (
    <div className="h-screen p-4">
      <div className="mb-4">
        <div className="inline-block px-3 py-1 rounded text-sm">
          Connection: 
          <span className={
            status === 'connected' ? 'text-green-600 ml-2' :
            status === 'connecting' ? 'text-yellow-600 ml-2' :
            'text-red-600 ml-2'
          }>
            {status}
          </span>
        </div>
      </div>
      
      <EventFeed 
        events={allEvents}
        autoScroll={true}
        showTimestamps={true}
      />
    </div>
  );
}

/**
 * Task-specific event feed example
 */
export function TaskEventFeedExample({ taskId }: { taskId: string }) {
  const taskEvents = useEventBuffer(taskId, { maxSize: 200 });
  
  return (
    <div className="h-full">
      <EventFeed 
        events={taskEvents}
        filterTaskId={taskId}
        compact={true}
        autoScroll={true}
      />
    </div>
  );
}

/**
 * Filtered event feed example - only show tool events
 */
export function ToolEventsFeedExample() {
  const allEvents = useEventBuffer(null, { maxSize: 300 });
  
  // Filter to only show tool-related events
  const toolEvents = allEvents.filter(e => 
    e.kind.startsWith('tool.')
  );
  
  return (
    <EventFeed 
      events={toolEvents}
      autoScroll={true}
      showTimestamps={true}
    />
  );
}
