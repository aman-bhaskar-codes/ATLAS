"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import { AtlasEventStream } from "../../lib/api/stream";
import { atlasApi } from "../../lib/api/client";
import { TaskEventSchema, isTerminal, type ConnectionState, type TaskEvent } from "../../lib/api/contracts";
import { reconcile } from "./reconcile";

const API_BASE =
  process.env.NEXT_PUBLIC_ATLAS_API_URL ?? "http://localhost:8730/api/v1";

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
  const stoppedRef = useRef<boolean>(false);
  const sourceRef = useRef<AtlasEventStream | null>(null);
  const generationRef = useRef(0);

  const updateEvents = useCallback((incoming: TaskEvent[]) => {
    const result = reconcile(eventsRef.current, incoming);
    eventsRef.current = result.events;
    setEvents([...result.events]);
    setHasGap(result.hasGap);
    setLastSyncedAt(new Date().toISOString());
  }, []);

  const resync = useCallback(async () => {
    if (!taskId) return;
    try {
      const generation = generationRef.current;
      const fresh = await atlasApi.taskEvents(taskId, 0);
      if (generation !== generationRef.current || stoppedRef.current) return;
      eventsRef.current = [];
      updateEvents(fresh);
      setHasGap(false);
    } catch {
      // resync failure is non-fatal — keep existing events
    }
  }, [taskId, updateEvents]);

  const connect = useCallback(() => {
    if (!taskId || stoppedRef.current) return;
    if (taskState && isTerminal(taskState)) return;

    const url = `${API_BASE}/tasks/${encodeURIComponent(taskId)}/events/stream`;

    sourceRef.current?.close();
    const source = new AtlasEventStream(url);
    sourceRef.current = source;

    source.onopen = () => {
      setConnection("connected");
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

    source.onerror = ({ retrying }) => {
      if (stoppedRef.current) return;
      setConnection(retrying ? "reconnecting" : "offline");
    };
  }, [taskId, taskState, updateEvents]);

  // Gap recovery: when a gap is detected, refetch everything
  useEffect(() => {
    if (hasGap) {
      const timer = setTimeout(() => { void resync(); }, 0);
      return () => clearTimeout(timer);
    }
  }, [hasGap, resync]);

  // Reconnect when page returns to foreground
  useEffect(() => {
    const handler = () => {
      if (!document.hidden && connection !== "connected" && taskId && taskState && !isTerminal(taskState)) {
        const generation = generationRef.current;
        void resync().then(() => {
          if (!stoppedRef.current && generation === generationRef.current) connect();
        });
      }
    };
    document.addEventListener("visibilitychange", handler);
    return () => document.removeEventListener("visibilitychange", handler);
  }, [connection, taskId, taskState, connect, resync]);

  useEffect(() => {
    if (!taskId) return;
    stoppedRef.current = false;
    eventsRef.current = [];
    generationRef.current += 1;

    if (taskState && isTerminal(taskState)) {
      // Task already terminal — just fetch the history, no SSE needed
      const generation = generationRef.current;
      atlasApi.taskEvents(taskId).then((fresh) => {
        if (!stoppedRef.current && generation === generationRef.current) updateEvents(fresh);
      }).catch(() => {});
      const timer = setTimeout(() => setConnection("offline"), 0);
      return () => { clearTimeout(timer); stoppedRef.current = true; generationRef.current += 1; };
    }

    connect();

    return () => {
      stoppedRef.current = true;
      generationRef.current += 1;
      sourceRef.current?.close();
      sourceRef.current = null;
    };
  }, [taskId, taskState, connect, updateEvents]); 

  return { events, connection, lastSyncedAt, hasGap, resync };
}
