/**
 * EventFeed Component
 * 
 * Displays a live-updating feed of events with:
 * - Auto-scroll to latest events
 * - Filtering by event type and task
 * - Event type badges with colors
 * - Expandable metadata
 * - Performance optimized for 1000+ events
 */

'use client';

import { useState, useEffect, useRef, useMemo } from 'react';
import type { AtlasEvent } from '../../lib/websocket';

export interface EventFeedProps {
  /** Array of events to display */
  events: AtlasEvent[];
  
  /** Filter by event type (e.g., "tool.completed") */
  filterEventType?: string;
  
  /** Filter by task ID */
  filterTaskId?: string;
  
  /** Auto-scroll to new events (default: true) */
  autoScroll?: boolean;
  
  /** Max events to display (default: 1000) */
  maxEvents?: number;
  
  /** Show timestamps (default: true) */
  showTimestamps?: boolean;
  
  /** Compact mode (less padding) */
  compact?: boolean;
}

interface EventCardProps {
  event: AtlasEvent;
  compact?: boolean;
}

function EventCard({ event, compact = false }: EventCardProps) {
  const [expanded, setExpanded] = useState(false);
  
  // Get event type badge color
  const getBadgeColor = (kind: string): string => {
    if (kind.includes('started') || kind.includes('building')) return 'bg-blue-100 text-blue-800';
    if (kind.includes('completed') || kind.includes('resolved')) return 'bg-green-100 text-green-800';
    if (kind.includes('failed') || kind.includes('denied')) return 'bg-red-100 text-red-800';
    if (kind.includes('thought') || kind.includes('action')) return 'bg-cyan-100 text-cyan-800';
    if (kind.includes('executing')) return 'bg-yellow-100 text-yellow-800';
    if (kind.includes('classified')) return 'bg-purple-100 text-purple-800';
    if (kind.includes('retrieved')) return 'bg-indigo-100 text-indigo-800';
    return 'bg-gray-100 text-gray-800';
  };
  
  // Get event symbol
  const getSymbol = (kind: string): string => {
    if (kind.includes('started') || kind.includes('building')) return '▶️';
    if (kind.includes('completed') || kind.includes('resolved')) return '✅';
    if (kind.includes('failed') || kind.includes('denied')) return '❌';
    if (kind.includes('thought') || kind.includes('action')) return '💭';
    if (kind.includes('executing')) return '⚙️';
    if (kind.includes('classified')) return '🛡️';
    if (kind.includes('retrieved')) return '📚';
    return '•';
  };
  
  // Format timestamp
  const formatTime = (ts?: string): string => {
    if (!ts) return '';
    try {
      const date = new Date(ts);
      return date.toLocaleTimeString('en-US', { 
        hour: '2-digit', 
        minute: '2-digit', 
        second: '2-digit' 
      });
    } catch {
      return ts.slice(11, 19); // HH:MM:SS from ISO string
    }
  };
  
  const badgeColor = getBadgeColor(event.kind);
  const symbol = getSymbol(event.kind);
  const hasMetadata = event.metadata && Object.keys(event.metadata).length > 0;
  
  return (
    <div 
      className={`border-l-4 ${
        event.historical ? 'border-gray-300 bg-gray-50' : 'border-blue-400 bg-white'
      } ${compact ? 'p-2' : 'p-3'} hover:bg-gray-50 transition-colors`}
    >
      <div className="flex items-start justify-between gap-2">
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            <span className="text-lg">{symbol}</span>
            
            <span className={`text-xs font-medium px-2 py-1 rounded ${badgeColor}`}>
              {event.kind}
            </span>
            
            {event.historical && (
              <span className="text-xs bg-gray-200 text-gray-600 px-2 py-1 rounded">
                REPLAY
              </span>
            )}
            
            {event.task_id && (
              <span className="text-xs font-mono text-gray-500">
                {event.task_id.slice(0, 8)}
              </span>
            )}
          </div>
          
          {event.metadata?.summary && (
            <p className={`text-gray-700 ${compact ? 'text-sm mt-1' : 'mt-2'}`}>
              {event.metadata.summary}
            </p>
          )}
          
          {hasMetadata && (
            <button
              onClick={() => setExpanded(!expanded)}
              className="text-xs text-blue-600 hover:text-blue-800 mt-1"
            >
              {expanded ? '▼ Hide details' : '▶ Show details'}
            </button>
          )}
          
          {expanded && event.metadata && (
            <div className="mt-2 bg-gray-100 rounded p-2 text-xs font-mono overflow-x-auto">
              <pre>{JSON.stringify(event.metadata, null, 2)}</pre>
            </div>
          )}
        </div>
        
        <div className="text-xs text-gray-500 whitespace-nowrap">
          {formatTime(event._timestamp)}
        </div>
      </div>
    </div>
  );
}

export function EventFeed({
  events,
  filterEventType,
  filterTaskId,
  autoScroll = true,
  maxEvents = 1000,
  showTimestamps = true,
  compact = false,
}: EventFeedProps) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const [isPaused, setIsPaused] = useState(false);
  const [searchQuery, setSearchQuery] = useState('');
  
  // Filter events
  const filteredEvents = useMemo(() => {
    let filtered = events;
    
    if (filterEventType) {
      filtered = filtered.filter(e => e.kind === filterEventType);
    }
    
    if (filterTaskId) {
      filtered = filtered.filter(e => e.task_id === filterTaskId);
    }
    
    if (searchQuery) {
      const query = searchQuery.toLowerCase();
      filtered = filtered.filter(e => 
        e.kind.toLowerCase().includes(query) ||
        e.task_id?.toLowerCase().includes(query) ||
        e.metadata?.summary?.toLowerCase().includes(query)
      );
    }
    
    // Limit to maxEvents (keep most recent)
    if (filtered.length > maxEvents) {
      return filtered.slice(-maxEvents);
    }
    
    return filtered;
  }, [events, filterEventType, filterTaskId, searchQuery, maxEvents]);
  
  // Auto-scroll to bottom when new events arrive
  useEffect(() => {
    if (!autoScroll || isPaused) return;
    
    const scrollEl = scrollRef.current;
    if (!scrollEl) return;
    
    // Scroll to bottom
    scrollEl.scrollTop = scrollEl.scrollHeight;
  }, [filteredEvents.length, autoScroll, isPaused]);
  
  return (
    <div className="flex flex-col h-full bg-white rounded-lg shadow">
      {/* Header with controls */}
      <div className="border-b p-3 space-y-2">
        <div className="flex items-center justify-between">
          <h3 className="font-semibold text-lg">
            Event Feed
            <span className="ml-2 text-sm text-gray-500">
              ({filteredEvents.length} events)
            </span>
          </h3>
          
          <div className="flex items-center gap-2">
            {/* Auto-scroll toggle */}
            <button
              onClick={() => setIsPaused(!isPaused)}
              className={`px-3 py-1 text-sm rounded ${
                isPaused 
                  ? 'bg-gray-200 text-gray-700' 
                  : 'bg-blue-100 text-blue-700'
              }`}
            >
              {isPaused ? '▶️ Resume' : '⏸️ Pause'}
            </button>
            
            {/* Clear button */}
            {filteredEvents.length > 0 && (
              <button
                onClick={() => window.location.reload()}
                className="px-3 py-1 text-sm bg-red-50 text-red-700 rounded hover:bg-red-100"
              >
                Clear
              </button>
            )}
          </div>
        </div>
        
        {/* Search */}
        <input
          type="text"
          placeholder="Search events..."
          value={searchQuery}
          onChange={(e) => setSearchQuery(e.target.value)}
          className="w-full px-3 py-2 text-sm border rounded focus:outline-none focus:ring-2 focus:ring-blue-500"
        />
      </div>
      
      {/* Event list */}
      <div 
        ref={scrollRef}
        className="flex-1 overflow-y-auto"
        style={{ maxHeight: 'calc(100vh - 250px)' }}
      >
        {filteredEvents.length === 0 ? (
          <div className="flex items-center justify-center h-full text-gray-500">
            <div className="text-center">
              <div className="text-4xl mb-2">📭</div>
              <div>No events yet</div>
              {(filterEventType || filterTaskId || searchQuery) && (
                <div className="text-sm mt-1">Try adjusting your filters</div>
              )}
            </div>
          </div>
        ) : (
          <div className="divide-y">
            {filteredEvents.map((event, index) => (
              <EventCard 
                key={`${event.correlation_id}-${index}`}
                event={event}
                compact={compact}
              />
            ))}
          </div>
        )}
      </div>
      
      {/* Footer with stats */}
      {isPaused && (
        <div className="border-t bg-yellow-50 p-2 text-center text-sm text-yellow-800">
          ⏸️ Auto-scroll paused - Click Resume to continue
        </div>
      )}
    </div>
  );
}
