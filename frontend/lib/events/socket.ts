// frontend/lib/events/socket.ts
import { TaskEventSchema, type TaskEvent } from "../api/contracts";
import { AtlasEventStream } from "../api/stream";

export function connectTaskEvents(
  taskId: string,
  onEvent: (event: TaskEvent) => void,
  onStatus: (status: "connected" | "reconnecting" | "closed") => void,
): () => void {
  const source = new AtlasEventStream(`/tasks/${encodeURIComponent(taskId)}/events/stream`);
  const seen = new Set<string>();
  source.onopen = () => onStatus("connected");
  const receive = (message: MessageEvent<string>) => {
    let json: unknown;
    try { json = JSON.parse(message.data); } catch { return; }
    const parsed = TaskEventSchema.safeParse(json);
    if (!parsed.success || seen.has(parsed.data.event_id)) return;
    seen.add(parsed.data.event_id);
    if (seen.size > 500) seen.delete(seen.values().next().value!);
    onEvent(parsed.data);
  };
  source.addEventListener("task_event", receive);
  source.addEventListener("message", receive);
  source.addEventListener("stream_closed", () => onStatus("closed"));
  source.onerror = ({ retrying }) => onStatus(retrying ? "reconnecting" : "closed");
  return () => { source.close(); onStatus("closed"); };
}
