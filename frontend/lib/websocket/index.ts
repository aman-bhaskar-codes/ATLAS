/**
 * ATLAS WebSocket layer.
 *
 * SCOPE (verified 2026-08-23): the live product streams task events over **SSE**
 * (`features/runtime-console/*`), not WebSockets. The only remaining consumer of
 * this module is `features/memory/useMemoryLive.ts` (the `/memory` live panel),
 * plus the `AtlasEvent` type used by the legacy event cards.
 *
 * The previously-exported `useTaskEvents` / `useGlobalEvents` / `useEventBuffer`
 * / `useMemoryEvents` hooks were removed: nothing imported them (their only
 * callers were prototype files that have been deleted), and they all defaulted
 * to `ws://localhost:8000` — a port ATLAS has never served. Keeping dead,
 * mis-configured hooks around invites exactly the kind of "looks wired, isn't"
 * surface this codebase forbids. Re-add them against a real port when a real
 * consumer exists.
 */

export interface AtlasEvent {
  correlation_id: string;
  task_id?: string;
  kind: string;
  state?: string;
  /**
   * Raw event payload as emitted by the backend bus. Deliberately untyped: the
   * legacy event cards render whatever the orchestrator attached, and the
   * per-kind payload contracts are not modelled on the frontend yet (tracked as
   * frontend debt — the modern SSE path in `lib/api/contracts.ts` IS typed).
   */
  // eslint-disable-next-line @typescript-eslint/no-explicit-any -- legacy untyped bus payload; see note above
  metadata?: Record<string, any>;
  _timestamp?: string;
  _topic?: string;
  historical?: boolean;
}

export { useWebSocket } from './useWebSocket';
export type { ConnectionStatus, WebSocketOptions, UseWebSocketReturn } from './useWebSocket';
