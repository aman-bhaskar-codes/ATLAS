"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import { atlasApi } from "../../lib/api/client";
import { TaskEventSchema, isTerminal, type ConnectionState, type TaskEvent } from "../../lib/api/contracts";
import { reconcile } from "./reconcile";

const API_BASE =
  process.env.NEXT_PUBLIC_ATLAS_API_URL ?? "http://localhost:8730/api/v1";

const BACKOFF_DELAYS = [500, 1000, 2000, 4000, 8000, 10_000] as const;

interface UseTaskEventsResult {
  events: TaskEvent[];
  connection: ConnectionState;
  lastSyncedAt: string | null;
  hasGap: boolean;
  resync: () => void;
}

export function useTaskEvents(
  taskId: string | null,
  taskState: string | null,
): UseTaskEventsResult {
  const [events, setEvents] = useState<TaskEvent[]>([]);
  const [connection, setConnection] = useState<ConnectionState>("offline");
  const [lastSyncedAt, setLastSyncedAt] = useState<string | null>(null);
  const [hasGap, setHasGap] = useState(false);

  const eventsRef = useRef<TaskEvent[]>([]);
  const lastSeqRef = useRef<number>(0);
  const backoffRef = useRef<number>(0);
  const stoppedRef = useRef<boolean>(false);
  const sourceRef = useRef<EventSource | null>(null);

  const updateEvents = useCallback((incoming: TaskEvent[]) => {
    const result = reconcile(eventsRef.current, incoming);
    eventsRef.current = result.events;
    lastSeqRef.current = result.lastSequence;
    setEvents([...result.events]);
    setHasGap(result.hasGap);
    setLastSyncedAt(new Date().toISOString());
  }, []);

  const resync = useCallback(async () => {
    if (!taskId) return;
    try {
      const fresh = await atlasApi.taskEvents(taskId, 0);
      eventsRef.current = [];
      updateEvents(fresh);
      setTimeout(() => setHasGap(false), 0);
    } catch {
      // resync failure is non-fatal — keep existing events
    }
  }, [taskId, updateEvents]);

  const connect = useCallback(() => {
    if (!taskId || stoppedRef.current) return;
    if (taskState && isTerminal(taskState)) {
      setTimeout(() => setConnection("offline"), 0);
      return;
    }

    const url = `${API_BASE}/tasks/${encodeURIComponent(taskId)}/events/stream`;

    const source = new EventSource(url);
    sourceRef.current = source;

    source.onopen = () => {
      setConnection("connected");
      backoffRef.current = 0;
    };

    source.addEventListener("task_event", (e: MessageEvent) => {
      try {
        const parsed = TaskEventSchema.safeParse(JSON.parse(e.data));
        if (parsed.success) updateEvents([parsed.data]);
      } catch {
        // malformed event — ignore
      }
    });

    source.addEventListener("stream_closed", () => {
      source.close();
      setConnection("offline");
    });

    source.addEventListener("heartbeat", () => {
      setLastSyncedAt(new Date().toISOString());
    });

    source.onerror = () => {
      source.close();
      sourceRef.current = null;
      if (stoppedRef.current) return;
      setConnection("reconnecting");
      const delay =
        BACKOFF_DELAYS[Math.min(backoffRef.current, BACKOFF_DELAYS.length - 1)];
      backoffRef.current += 1;
      window.setTimeout(() => {
        if (!stoppedRef.current) {
          /* eslint-disable */
          connect();
          /* eslint-enable */
        }
      }, delay);
    };
  }, [taskId, taskState, updateEvents]);

  // Gap recovery: when a gap is detected, refetch everything
  useEffect(() => {
    if (hasGap) {
      setTimeout(() => { void resync(); }, 0);
    }
  }, [hasGap, resync]);

  // Reconnect when page returns to foreground
  useEffect(() => {
    const handler = () => {
      if (!document.hidden && connection !== "connected" && taskId && taskState && !isTerminal(taskState)) {
        void resync().then(() => connect());
      }
    };
    document.addEventListener("visibilitychange", handler);
    return () => document.removeEventListener("visibilitychange", handler);
  }, [connection, taskId, taskState, connect, resync]);

  useEffect(() => {
    if (!taskId) return;
    stoppedRef.current = false;
    eventsRef.current = [];
    lastSeqRef.current = 0;
    backoffRef.current = 0;

    if (taskState && isTerminal(taskState)) {
      // Task already terminal — just fetch the history, no SSE needed
      atlasApi.taskEvents(taskId).then(updateEvents).catch(() => {});
      setTimeout(() => setConnection("offline"), 0);
      return;
    }

    connect();

    return () => {
      stoppedRef.current = true;
      sourceRef.current?.close();
      sourceRef.current = null;
      setTimeout(() => setConnection("offline"), 0);
    };
  }, [taskId, taskState, connect, updateEvents]); 

  return { events, connection, lastSyncedAt, hasGap, resync };
}
