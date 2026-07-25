// frontend/lib/events/socket.ts
import { TaskEventSchema, type TaskEvent } from "../api/contracts";

export function connectTaskEvents(
  taskId: string,
  onEvent: (event: TaskEvent) => void,
  onStatus: (status: "connected" | "reconnecting" | "closed") => void,
): () => void {
  const url = `${process.env.NEXT_PUBLIC_ATLAS_API_URL ?? "http://localhost:8730/api/v1"}/tasks/${encodeURIComponent(taskId)}/events/stream`;
  const seen = new Set<string>();
  let source: EventSource | null = null;
  let stopped = false;
  let delay = 500;

  const connect = () => {
    if (stopped) return;
    source = new EventSource(url);
    source.onopen = () => { delay = 500; onStatus("connected"); };
    source.onmessage = (message) => {
      const parsed = TaskEventSchema.safeParse(JSON.parse(message.data));
      if (!parsed.success || seen.has(parsed.data.event_id)) return;
      seen.add(parsed.data.event_id);
      if (seen.size > 500) seen.delete(seen.values().next().value!);
      onEvent(parsed.data);
    };
    source.onerror = () => {
      source?.close();
      if (stopped) return;
      onStatus("reconnecting");
      window.setTimeout(connect, delay);
      delay = Math.min(delay * 2, 10_000);
    };
  };

  connect();
  return () => { stopped = true; source?.close(); onStatus("closed"); };
}
