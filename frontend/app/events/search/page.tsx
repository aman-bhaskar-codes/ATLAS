/**
 * Event Search Page
 * 
 * Historical event search with filters and results display
 */

'use client';

import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { EventFeed } from '../../../components/events';
import type { AtlasEvent } from '../../../lib/websocket';

interface SearchParams {
  task_id?: string;
  topic?: string;
  from_ts?: string;
  to_ts?: string;
  limit: number;
  offset: number;
}

interface SearchResult {
  events: AtlasEvent[];
  total: number;
  limit: number;
  offset: number;
}

async function searchEvents(params: SearchParams): Promise<SearchResult> {
  // NEXT_PUBLIC_ATLAS_API_URL already includes the /api/v1 prefix, so the path
  // must NOT repeat it (doing so produced /api/v1/api/v1/... -> 404). The
  // fallback matches the port ATLAS actually serves (8730).
  const baseUrl = process.env.NEXT_PUBLIC_ATLAS_API_URL || 'http://localhost:8730/api/v1';
  const url = new URL(`${baseUrl}/events/search`);
  
  Object.entries(params).forEach(([key, value]) => {
    if (value !== null && value !== undefined && value !== '') {
      url.searchParams.set(key, String(value));
    }
  });
  
  const response = await fetch(url.toString());
  if (!response.ok) throw new Error('Search failed');
  return response.json();
}

export default function EventSearchPage() {
  const [params, setParams] = useState<SearchParams>({
    task_id: '',
    topic: '',
    from_ts: '',
    to_ts: '',
    limit: 100,
    offset: 0,
  });
  
  const [searchKey, setSearchKey] = useState(0);
  
  const { data, isLoading, error } = useQuery({
    queryKey: ['event-search', searchKey, params],
    queryFn: () => searchEvents(params),
    enabled: searchKey > 0,
  });
  
  const handleSearch = () => {
    setSearchKey(prev => prev + 1);
  };
  
  const handleClear = () => {
    setParams({
      task_id: '',
      topic: '',
      from_ts: '',
      to_ts: '',
      limit: 100,
      offset: 0,
    });
    setSearchKey(0);
  };
  
  const handleKeyPress = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter') {
      handleSearch();
    }
  };
  
  return (
    <div className="p-6 max-w-7xl mx-auto">
      <h1 className="text-3xl font-bold mb-6">Event Search</h1>
      
      <div className="grid grid-cols-1 lg:grid-cols-4 gap-6">
        {/* Search Form */}
        <div className="lg:col-span-1">
          <div className="bg-white rounded-lg shadow p-6 sticky top-6">
            <h2 className="text-lg font-semibold mb-4">Filters</h2>
            
            <div className="space-y-4">
              <div>
                <label className="block text-sm font-medium mb-1">Task ID</label>
                <input
                  type="text"
                  value={params.task_id}
                  onChange={(e) => setParams({...params, task_id: e.target.value})}
                  onKeyPress={handleKeyPress}
                  className="w-full px-3 py-2 border rounded focus:ring-2 focus:ring-blue-500"
                  placeholder="Optional"
                />
              </div>
              
              <div>
                <label className="block text-sm font-medium mb-1">Event Type</label>
                <input
                  type="text"
                  value={params.topic}
                  onChange={(e) => setParams({...params, topic: e.target.value})}
                  onKeyPress={handleKeyPress}
                  className="w-full px-3 py-2 border rounded focus:ring-2 focus:ring-blue-500"
                  placeholder="e.g., tool.completed"
                />
              </div>
              
              <div>
                <label className="block text-sm font-medium mb-1">From Time</label>
                <input
                  type="datetime-local"
                  value={params.from_ts}
                  onChange={(e) => setParams({...params, from_ts: e.target.value})}
                  className="w-full px-3 py-2 border rounded focus:ring-2 focus:ring-blue-500"
                />
              </div>
              
              <div>
                <label className="block text-sm font-medium mb-1">To Time</label>
                <input
                  type="datetime-local"
                  value={params.to_ts}
                  onChange={(e) => setParams({...params, to_ts: e.target.value})}
                  className="w-full px-3 py-2 border rounded focus:ring-2 focus:ring-blue-500"
                />
              </div>
              
              <div>
                <label className="block text-sm font-medium mb-1">Limit</label>
                <input
                  type="number"
                  value={params.limit}
                  onChange={(e) => setParams({...params, limit: parseInt(e.target.value) || 100})}
                  className="w-full px-3 py-2 border rounded focus:ring-2 focus:ring-blue-500"
                  min="1"
                  max="1000"
                />
              </div>
              
              <div className="flex gap-2">
                <button
                  onClick={handleSearch}
                  className="flex-1 bg-blue-600 text-white px-4 py-2 rounded hover:bg-blue-700 font-medium transition-colors"
                >
                  🔍 Search
                </button>
                <button
                  onClick={handleClear}
                  className="px-4 py-2 border rounded hover:bg-gray-50 transition-colors"
                  title="Clear filters"
                >
                  ✕
                </button>
              </div>
            </div>
          </div>
        </div>
        
        {/* Results */}
        <div className="lg:col-span-3">
          <div className="bg-white rounded-lg shadow p-6">
            {searchKey === 0 ? (
              <div className="text-center py-12 text-gray-500">
                <div className="text-4xl mb-4">🔍</div>
                <div className="text-lg font-medium mb-2">Search Historical Events</div>
                <div className="text-sm">Enter search criteria and click Search</div>
              </div>
            ) : isLoading ? (
              <div className="text-center py-12">
                <div className="text-2xl mb-2">⏳</div>
                <div>Searching...</div>
              </div>
            ) : error ? (
              <div className="text-center py-12 text-red-600">
                <div className="text-2xl mb-2">❌</div>
                <div>Error: {(error as Error).message}</div>
              </div>
            ) : data ? (
              <>
                <div className="mb-4 flex justify-between items-center">
                  <h2 className="text-lg font-semibold">
                    Results ({data.total} total)
                  </h2>
                  <span className="text-sm text-gray-600">
                    Showing {data.offset + 1}-{data.offset + data.events.length}
                  </span>
                </div>
                
                {data.events.length === 0 ? (
                  <div className="text-center py-12 text-gray-500">
                    <div className="text-4xl mb-2">📭</div>
                    <div>No events found matching your criteria</div>
                  </div>
                ) : (
                  <div className="max-h-[600px] overflow-y-auto">
                    <EventFeed events={data.events} autoScroll={false} />
                  </div>
                )}
                
                {/* Pagination */}
                {data.total > params.limit && (
                  <div className="mt-6 flex justify-center gap-2 items-center">
                    <button
                      onClick={() => setParams({...params, offset: Math.max(0, params.offset - params.limit)})}
                      disabled={params.offset === 0}
                      className="px-4 py-2 border rounded disabled:opacity-50 disabled:cursor-not-allowed hover:bg-gray-50 transition-colors"
                    >
                      ← Previous
                    </button>
                    <span className="text-sm text-gray-600">
                      Page {Math.floor(params.offset / params.limit) + 1} of {Math.ceil(data.total / params.limit)}
                    </span>
                    <button
                      onClick={() => setParams({...params, offset: params.offset + params.limit})}
                      disabled={params.offset + params.limit >= data.total}
                      className="px-4 py-2 border rounded disabled:opacity-50 disabled:cursor-not-allowed hover:bg-gray-50 transition-colors"
                    >
                      Next →
                    </button>
                  </div>
                )}
              </>
            ) : null}
          </div>
        </div>
      </div>
    </div>
  );
}
