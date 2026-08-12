# WebSocket Hooks for ATLAS

React hooks for real-time event streaming from ATLAS backend.

## Features

✅ **Auto-reconnect** with exponential backoff  
✅ **Connection state tracking** (connecting, connected, disconnected, error)  
✅ **Ping/pong handling** automatic keepalive  
✅ **TypeScript** fully typed with generics  
✅ **Historical replay** for task-scoped streams  
✅ **Event buffering** accumulate events into arrays  

## Installation

These hooks are already available in the project:

```tsx
import { useTaskEvents, useGlobalEvents, useEventBuffer } from '@/lib/websocket';
```

## Quick Start

### Monitor a Specific Task

```tsx
import { useTaskEvents } from '@/lib/websocket';

function TaskMonitor({ taskId }: { taskId: string }) {
  const { data: event, status } = useTaskEvents(taskId);
  
  return (
    <div>
      <div>Status: {status}</div>
      {event && <div>Latest: {event.kind}</div>}
    </div>
  );
}
```

### Monitor All System Events

```tsx
import { useGlobalEvents } from '@/lib/websocket';

function Dashboard() {
  const { data: event, status, error } = useGlobalEvents();
  
  return (
    <div>
      {status === 'connected' && <div className="text-green-600">● Live</div>}
      {event && <EventCard event={event} />}
    </div>
  );
}
```

### Accumulate Event History

```tsx
import { useEventBuffer } from '@/lib/websocket';

function EventLog({ taskId }: { taskId: string }) {
  const events = useEventBuffer(taskId, { maxSize: 100 });
  
  return (
    <ul>
      {events.map((event, i) => (
        <li key={i}>{event.kind}</li>
      ))}
    </ul>
  );
}
```

## API Reference

### `useTaskEvents(taskId, options?)`

Subscribe to events for a specific task with historical replay.

**Parameters:**
- `taskId: string | null` - Task ID to monitor (null = no connection)
- `options?: WebSocketOptions` - Configuration options

**Returns:** `UseWebSocketReturn<AtlasEvent>`
- `data: AtlasEvent | null` - Latest event received
- `status: ConnectionStatus` - Connection state
- `error: Error | null` - Error if any
- `send: (data) => void` - Send message to server
- `reconnect: () => void` - Manually reconnect
- `close: () => void` - Close connection

### `useGlobalEvents(options?)`

Subscribe to all system events (global firehose).

**Parameters:**
- `options?: WebSocketOptions` - Configuration options

**Returns:** `UseWebSocketReturn<AtlasEvent>`

### `useEventBuffer(taskId, options?)`

Accumulate events into a buffer array.

**Parameters:**
- `taskId: string | null` - Task ID to monitor
- `options?: { maxSize?: number }` - Max buffer size (default: 1000)

**Returns:** `AtlasEvent[]` - Array of events

### `WebSocketOptions`

Configuration options for WebSocket connections:

```typescript
interface WebSocketOptions {
  autoReconnect?: boolean;        // Default: true
  reconnectDelay?: number;        // Default: 1000ms
  maxReconnectDelay?: number;     // Default: 30000ms
  reconnectMultiplier?: number;   // Default: 2
  handlePing?: boolean;           // Default: true
  debug?: boolean;                // Default: false
}
```

### `AtlasEvent`

Event data structure from ATLAS backend:

```typescript
interface AtlasEvent {
  correlation_id: string;
  task_id?: string;
  kind: string;                   // e.g., "task.started", "tool.completed"
  state?: string;
  metadata?: Record<string, any>;
  _timestamp?: string;
  _topic?: string;
  historical?: boolean;           // True if from replay
}
```

## Connection States

- **`connecting`** - Establishing connection
- **`connected`** - Active and receiving events
- **`disconnected`** - Connection lost (will auto-reconnect)
- **`error`** - Connection error occurred

## Environment Variables

Set the WebSocket server URL via environment variable:

```env
NEXT_PUBLIC_ATLAS_WS_URL=ws://localhost:8000
```

Default: `ws://localhost:8000`

## Advanced Usage

### Custom Reconnect Strategy

```tsx
const { status, reconnect } = useTaskEvents(taskId, {
  autoReconnect: true,
  reconnectDelay: 2000,      // Start with 2s
  maxReconnectDelay: 60000,  // Max 60s
  reconnectMultiplier: 1.5,  // Slower backoff
});

// Manual reconnect on button click
<button onClick={reconnect}>Reconnect</button>
```

### Debug Logging

```tsx
const { data } = useGlobalEvents({ debug: true });
// Logs to console: connections, messages, errors
```

### Handle Connection Status

```tsx
function ConnectionIndicator() {
  const { status } = useGlobalEvents();
  
  return (
    <div className={
      status === 'connected' ? 'bg-green-500' :
      status === 'connecting' ? 'bg-yellow-500' :
      'bg-red-500'
    }>
      {status}
    </div>
  );
}
```

## Testing

The hooks handle edge cases automatically:

- **Network loss:** Auto-reconnect with backoff
- **Server restart:** Reconnect and replay missed events
- **Component unmount:** Clean disconnect, cancel reconnects
- **Duplicate events:** Historical replay deduplication

## Examples

See `examples.tsx` for complete working examples:
- Task monitoring with status
- Global event stream with stats
- Event history with buffering

## Backend Endpoints

These hooks connect to Phase 1 WebSocket endpoints:

- `/ws/tasks/{id}/stream` - Task-scoped with historical replay
- `/ws/events` - Global event firehose

See Phase 1 documentation for backend details.
