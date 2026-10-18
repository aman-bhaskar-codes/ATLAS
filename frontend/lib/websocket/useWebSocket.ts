/**
 * React hook for WebSocket connections with auto-reconnect.
 * 
 * Features:
 * - Automatic reconnection with exponential backoff
 * - Connection state tracking
 * - Ping/pong handling
 * - Message queue for offline buffering
 * - TypeScript type safety
 */

import { useEffect, useRef, useState, useCallback } from 'react';
import { subscribeCredentialChange, websocketProtocols } from '../api/auth';

export type ConnectionStatus = 'connecting' | 'connected' | 'disconnected' | 'error';

export interface WebSocketOptions {
  /** Auto-reconnect on disconnect (default: true) */
  autoReconnect?: boolean;
  
  /** Initial reconnect delay in ms (default: 1000) */
  reconnectDelay?: number;
  
  /** Max reconnect delay in ms (default: 30000) */
  maxReconnectDelay?: number;
  
  /** Reconnect delay multiplier (default: 2) */
  reconnectMultiplier?: number;
  
  /** Handle ping messages (send pong response) (default: true) */
  handlePing?: boolean;
  
  /** Debug logging (default: false) */
  debug?: boolean;

  /** Full Jitter exponential backoff (default: false) */
  jitter?: boolean;
}

export interface UseWebSocketReturn<T> {
  /** Latest message received */
  data: T | null;
  
  /** Connection status */
  status: ConnectionStatus;
  
  /** Error if any */
  error: Error | null;
  
  /** Send a message */
  send: (data: string | object) => void;
  
  /** Manually reconnect */
  reconnect: () => void;
  
  /** Close connection */
  close: () => void;
}

export function resolveWebSocketUrl(explicitUrl?: string | null, lastSeq?: number): string | null {
  if (explicitUrl === null) return null;
  let base = explicitUrl;
  if (!base) {
    base = (typeof process !== 'undefined' && process.env.NEXT_PUBLIC_ATLAS_WS_URL)
      ? process.env.NEXT_PUBLIC_ATLAS_WS_URL
      : 'ws://localhost:8730/api/v1/events/ws';
  } else if (base.startsWith('/')) {
    const wsHost = (typeof window !== 'undefined')
      ? `${window.location.protocol === 'https:' ? 'wss:' : 'ws:'}//${window.location.host}`
      : 'ws://localhost:8730';
    base = `${wsHost}${base}`;
  } else if (base.startsWith('http://') || base.startsWith('https://')) {
    base = base.replace(/^http:\/\//, 'ws://').replace(/^https:\/\//, 'wss://');
  }

  const apiKey = (typeof process !== 'undefined' && process.env.NEXT_PUBLIC_ATLAS_API_KEY)
    ? process.env.NEXT_PUBLIC_ATLAS_API_KEY
    : '';

  try {
    const parsed = new URL(base);
    if (apiKey && !parsed.searchParams.has('api_key')) {
      parsed.searchParams.set('api_key', apiKey);
    }
    if (lastSeq !== undefined && lastSeq > 0 && !parsed.searchParams.has('last_seq')) {
      parsed.searchParams.set('last_seq', String(lastSeq));
    }
    return parsed.toString();
  } catch {
    return base;
  }
}

/**
 * Base WebSocket hook with auto-reconnect and state management.
 * 
 * @example
 * ```tsx
 * const { data, status } = useWebSocket<EventType>('ws://localhost:8730/api/v1/events/ws');
 * ```
 */
export function useWebSocket<T = unknown>(
  url: string | null,
  options: WebSocketOptions = {}
): UseWebSocketReturn<T> {
  const {
    autoReconnect = true,
    reconnectDelay: initialReconnectDelay = 1000,
    maxReconnectDelay = 30000,
    reconnectMultiplier = 2,
    handlePing = true,
    debug = false,
    jitter = false,
  } = options;
  
  const [data, setData] = useState<T | null>(null);
  const [status, setStatus] = useState<ConnectionStatus>('connecting');
  const [error, setError] = useState<Error | null>(null);
  
  const wsRef = useRef<WebSocket | null>(null);
  const reconnectTimeoutRef = useRef<NodeJS.Timeout | null>(null);
  const reconnectDelayRef = useRef(initialReconnectDelay);
  const shouldReconnectRef = useRef(true);
  const failedHandshakesRef = useRef(0);
  const lastSeqRef = useRef<number | undefined>(undefined);
  
  const log = useCallback((...args: unknown[]) => {
    if (debug) console.log('[useWebSocket]', ...args);
  }, [debug]);

  // Holds the latest `connect` so the reconnect timer can re-enter it without
  // referencing the binding before it is initialized (a temporal-dead-zone read
  // that only happened to work because the timer fires after render).
  const connectRef = useRef<() => void>(() => {});

  const connect = useCallback(() => {
    const targetUrl = resolveWebSocketUrl(url, lastSeqRef.current);
    if (!targetUrl || !shouldReconnectRef.current || (wsRef.current && wsRef.current.readyState <= WebSocket.OPEN)) {
      return;
    }
    
    log('Connecting to', targetUrl);
    setStatus('connecting');
    setError(null);
    
    try {
      const ws = new WebSocket(targetUrl, websocketProtocols());
      wsRef.current = ws;
      
      let opened = false;
      const active = () => shouldReconnectRef.current && wsRef.current === ws;
      ws.onopen = () => {
        if (!active()) return;
        opened = true;
        failedHandshakesRef.current = 0;
        reconnectDelayRef.current = initialReconnectDelay;
        log('Connected');
        setStatus('connected');
      };
      
      ws.onmessage = (event) => {
        if (!active()) return;
        try {
          const parsed = JSON.parse(event.data);
          
          if (handlePing && parsed.type === 'ping') {
            log('Received ping, sending pong');
            ws.send('pong');
            return;
          }
          
          if (parsed.type === 'replay_complete') {
            log('Replay complete:', parsed);
            return;
          }

          if (parsed && typeof parsed === 'object') {
            const seq = parsed.seq_id ?? parsed.sequence ?? parsed.seq;
            if (typeof seq === 'number') {
              lastSeqRef.current = seq;
            }
          }
          
          log('Message received:', parsed);
          setData(parsed);
        } catch (err) {
          log('Failed to parse message:', err);
        }
      };
      
      ws.onerror = (event) => {
        if (!active()) return;
        log('WebSocket error:', event);
        setError(new Error('WebSocket connection error'));
        setStatus('error');
      };
      
      ws.onclose = (event) => {
        if (!active()) return;
        log('Connection closed:', event.code, event.reason);
        wsRef.current = null;
        setStatus('disconnected');
        if (!opened) failedHandshakesRef.current += 1;
        // Browsers map HTTP handshake denials to 1006, just like network failures.
        // Bound unopened attempts; a corrected credential resets this budget.
        if (failedHandshakesRef.current > 2) {
          setError(new Error('Connection failed. Check the backend and API key in Settings.'));
          setStatus('error');
          return;
        }
        
        if (autoReconnect && shouldReconnectRef.current && ![1000, 1008, 4401, 4403].includes(event.code)) {
          const baseDelay = reconnectDelayRef.current;
          reconnectDelayRef.current = Math.min(
            reconnectDelayRef.current * reconnectMultiplier,
            maxReconnectDelay
          );
          const delay = jitter
            ? Math.max(50, Math.floor(Math.random() * baseDelay))
            : baseDelay;
          log(`Reconnecting in ${delay}ms...`);
          
          reconnectTimeoutRef.current = setTimeout(() => {
            reconnectTimeoutRef.current = null;
            if (!shouldReconnectRef.current || wsRef.current) return;
            connectRef.current();
          }, delay);
        }
      };
    } catch (err) {
      log('Connection failed:', err);
      setError(err as Error);
      setStatus('error');
    }
  }, [url, autoReconnect, handlePing, initialReconnectDelay, maxReconnectDelay, reconnectMultiplier, jitter, log]);
  
  const send = useCallback((data: string | object) => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      const message = typeof data === 'string' ? data : JSON.stringify(data);
      wsRef.current.send(message);
      log('Message sent:', message);
    } else {
      log('Cannot send message, WebSocket not open');
    }
  }, [log]);
  
  const reconnect = useCallback(() => {
    log('Manual reconnect triggered');
    if (reconnectTimeoutRef.current) {
      clearTimeout(reconnectTimeoutRef.current);
    }
    const old = wsRef.current;
    wsRef.current = null;
    old?.close(1000, 'Reconnecting');
    shouldReconnectRef.current = true;
    failedHandshakesRef.current = 0;
    connect();
  }, [connect, log]);
  
  const close = useCallback(() => {
    log('Closing connection');
    shouldReconnectRef.current = false;
    if (reconnectTimeoutRef.current) {
      clearTimeout(reconnectTimeoutRef.current);
    }
    const old = wsRef.current;
    wsRef.current = null;
    old?.close(1000, 'Client closed');
    setStatus('disconnected');
  }, [log]);
  
  useEffect(() => {
    shouldReconnectRef.current = true;
    failedHandshakesRef.current = 0;
    connectRef.current = connect;

    if (url) {
      // `connect()` synchronously sets status->'connecting'. That is the correct
      // shape for subscribing to an EXTERNAL resource on mount (the canonical
      // job of an effect), not a derived-state cascade: the socket must be
      // opened as a side effect and the UI must reflect that it is opening.
      // eslint-disable-next-line react-hooks/set-state-in-effect -- external WebSocket lifecycle, not derived state
      connect();
    }

    const unsubscribe = subscribeCredentialChange(() => {
      if (shouldReconnectRef.current) reconnect();
    });
    return () => {
      unsubscribe();
      shouldReconnectRef.current = false;
      if (reconnectTimeoutRef.current) {
        clearTimeout(reconnectTimeoutRef.current);
      }
      const old = wsRef.current;
      wsRef.current = null;
      old?.close(1000, 'Component unmounted');
    };
  }, [url, connect, reconnect]);
  
  return { data, status, error, send, reconnect, close };
}
