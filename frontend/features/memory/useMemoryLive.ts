/**
 * useMemoryLive — subscribes to /ws/memory/live and accumulates a rolling
 * buffer of MemoryEvents alongside an up-to-date snapshot.
 *
 * WHY a dedicated hook instead of reusing useGlobalEvents: the global stream
 * delivers all event types.  Memory clients only care about MemoryEvents from
 * the dedicated memory broadcaster.  A focused hook keeps render logic clean.
 */

'use client';

import { useState, useEffect, useCallback } from 'react';
import { useWebSocket } from '../../lib/websocket';
import type { MemoryEvent, MemorySnapshot } from './contracts';

const WS_BASE =
  (typeof process !== 'undefined' && process.env.NEXT_PUBLIC_ATLAS_WS_URL)
    ? process.env.NEXT_PUBLIC_ATLAS_WS_URL
    : 'ws://localhost:8730';

const MEMORY_WS_URL = `${WS_BASE}/ws/memory/live`;

// ---------------------------------------------------------------------------
// Public hook
// ---------------------------------------------------------------------------

export interface MemoryLiveState {
  /** Rolling buffer — newest first, capped at maxBuffer */
  events: MemoryEvent[];
  /** Latest snapshot sent by the server on connect */
  snapshot: MemorySnapshot | null;
  /** WebSocket connection status */
  status: 'connecting' | 'connected' | 'disconnected' | 'error';
  /** Counts how many updates have arrived since mount */
  updateCount: number;
  /** Clear the local event buffer */
  clearEvents: () => void;
}

export function useMemoryLive(maxBuffer = 200): MemoryLiveState {
  const [events, setEvents] = useState<MemoryEvent[]>([]);
  const [snapshot, setSnapshot] = useState<MemorySnapshot | null>(null);
  const [updateCount, setUpdateCount] = useState(0);

  const { data: rawMessage, status } = useWebSocket<unknown>(MEMORY_WS_URL, {
    autoReconnect: true,
    reconnectDelay: 1500,
    maxReconnectDelay: 30_000,
    handlePing: true,
    debug: false,
  });

  // Process every new message from the WebSocket
  useEffect(() => {
    if (!rawMessage) return;

    const msg = rawMessage as Record<string, unknown>;

    // Server-sent snapshot on fresh connect
    if (msg['type'] === 'snapshot') {
      // Accumulating an inbound stream into local state is what this hook is
      // FOR: each message arrives from an external WebSocket, so the write
      // cannot be derived at render time. The lint rule targets derived-state
      // cascades, which this is not.
      // eslint-disable-next-line react-hooks/set-state-in-effect -- accumulating an external event stream
      setSnapshot(msg as unknown as MemorySnapshot);
      return;
    }

    // Ignore control messages
    if (msg['type'] === 'ping' || msg['type'] === 'replay_complete') return;

    // Otherwise it's a MemoryEvent
    if (msg['kind'] && typeof msg['kind'] === 'string' &&
        (msg['kind'] as string).startsWith('memory.')) {
      const event = msg as unknown as MemoryEvent;
      setEvents(prev => {
        const next = [event, ...prev];
        return next.length > maxBuffer ? next.slice(0, maxBuffer) : next;
      });
      setUpdateCount(c => c + 1);
    }
  }, [rawMessage, maxBuffer]);

  const clearEvents = useCallback(() => setEvents([]), []);

  return { events, snapshot, status, updateCount, clearEvents };
}

// ---------------------------------------------------------------------------
// Convenience: per-layer live counts derived from the event stream
// ---------------------------------------------------------------------------

export interface LiveCounts {
  episodic: number;
  semantic: number;
  user_model: number;
  knowledge: number;
}

/** Derives per-layer write counts from the live event buffer. */
export function useLiveCounts(events: MemoryEvent[]): LiveCounts {
  return events.reduce<LiveCounts>(
    (acc, ev) => {
      if (ev.kind === 'memory.stored' && ev.memory_type === 'episodic') acc.episodic++;
      else if (ev.kind === 'memory.fact_added' || ev.memory_type === 'semantic') acc.semantic++;
      else if (ev.kind === 'memory.user_model_updated') acc.user_model++;
      else if (ev.kind === 'memory.knowledge_indexed') acc.knowledge++;
      return acc;
    },
    { episodic: 0, semantic: 0, user_model: 0, knowledge: 0 },
  );
}
