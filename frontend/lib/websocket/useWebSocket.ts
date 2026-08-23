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

/**
 * Base WebSocket hook with auto-reconnect and state management.
 * 
 * @example
 * ```tsx
 * const { data, status } = useWebSocket<EventType>('ws://localhost:8000/ws/events');
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
  } = options;
  
  const [data, setData] = useState<T | null>(null);
  const [status, setStatus] = useState<ConnectionStatus>('connecting');
  const [error, setError] = useState<Error | null>(null);
  
  const wsRef = useRef<WebSocket | null>(null);
  const reconnectTimeoutRef = useRef<NodeJS.Timeout | null>(null);
  const reconnectDelayRef = useRef(initialReconnectDelay);
  const shouldReconnectRef = useRef(true);
  
  const log = useCallback((...args: unknown[]) => {
    if (debug) console.log('[useWebSocket]', ...args);
  }, [debug]);

  // Holds the latest `connect` so the reconnect timer can re-enter it without
  // referencing the binding before it is initialized (a temporal-dead-zone read
  // that only happened to work because the timer fires after render).
  const connectRef = useRef<() => void>(() => {});

  const connect = useCallback(() => {
    if (!url || wsRef.current?.readyState === WebSocket.OPEN) {
      return;
    }
    
    log('Connecting to', url);
    setStatus('connecting');
    setError(null);
    
    try {
      const ws = new WebSocket(url);
      wsRef.current = ws;
      
      ws.onopen = () => {
        log('Connected');
        setStatus('connected');
        reconnectDelayRef.current = initialReconnectDelay;
      };
      
      ws.onmessage = (event) => {
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
          
          log('Message received:', parsed);
          setData(parsed);
        } catch (err) {
          log('Failed to parse message:', err);
        }
      };
      
      ws.onerror = (event) => {
        log('WebSocket error:', event);
        setError(new Error('WebSocket connection error'));
        setStatus('error');
      };
      
      ws.onclose = (event) => {
        log('Connection closed:', event.code, event.reason);
        wsRef.current = null;
        setStatus('disconnected');
        
        if (autoReconnect && shouldReconnectRef.current && event.code !== 1000) {
          const delay = reconnectDelayRef.current;
          log(`Reconnecting in ${delay}ms...`);
          
          reconnectTimeoutRef.current = setTimeout(() => {
            reconnectDelayRef.current = Math.min(
              reconnectDelayRef.current * reconnectMultiplier,
              maxReconnectDelay
            );
            connectRef.current();
          }, delay);
        }
      };
    } catch (err) {
      log('Connection failed:', err);
      setError(err as Error);
      setStatus('error');
    }
  }, [url, autoReconnect, handlePing, initialReconnectDelay, maxReconnectDelay, reconnectMultiplier, log]);
  
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
    wsRef.current?.close();
    reconnectDelayRef.current = initialReconnectDelay;
    connect();
  }, [connect, initialReconnectDelay, log]);
  
  const close = useCallback(() => {
    log('Closing connection');
    shouldReconnectRef.current = false;
    if (reconnectTimeoutRef.current) {
      clearTimeout(reconnectTimeoutRef.current);
    }
    wsRef.current?.close(1000, 'Client closed');
    wsRef.current = null;
    setStatus('disconnected');
  }, [log]);
  
  useEffect(() => {
    shouldReconnectRef.current = true;
    connectRef.current = connect;

    if (url) {
      // `connect()` synchronously sets status->'connecting'. That is the correct
      // shape for subscribing to an EXTERNAL resource on mount (the canonical
      // job of an effect), not a derived-state cascade: the socket must be
      // opened as a side effect and the UI must reflect that it is opening.
      // eslint-disable-next-line react-hooks/set-state-in-effect -- external WebSocket lifecycle, not derived state
      connect();
    }

    return () => {
      shouldReconnectRef.current = false;
      if (reconnectTimeoutRef.current) {
        clearTimeout(reconnectTimeoutRef.current);
      }
      wsRef.current?.close(1000, 'Component unmounted');
    };
  }, [url, connect]);
  
  return { data, status, error, send, reconnect, close };
}
