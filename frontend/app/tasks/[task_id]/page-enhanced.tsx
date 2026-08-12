/**
 * Enhanced Task Detail Page with Live WebSocket Updates
 * 
 * Shows task information and live event stream using Phase 1 WebSocket infrastructure.
 * This is the Phase 2 enhanced version with real-time updates.
 */

'use client';

import { use } from 'react';
import { useQuery } from '@tanstack/react-query';
import { useTaskEvents, useEventBuffer } from '../../../lib/websocket';
import { EventFeed } from '../../../components/events';

interface Task {
  id: string;
  state: string;
  source: string;
  payload: any;
  created_ts: string;
  updated_ts: string;
}

async function fetchTask(taskId: string): Promise<Task> {
  const baseUrl = process.env.NEXT_PUBLIC_ATLAS_API_URL || 'http://localhost:8000';
  const response = await fetch(`${baseUrl}/api/v1/tasks/${taskId}`);
  
  if (!response.ok) {
    throw new Error(`Failed to fetch task: ${response.statusText}`);
  }
  
  return response.json();
}

function TaskHeader({ task, connectionStatus }: { task: Task; connectionStatus: string }) {
  const getStateColor = (state: string): string => {
    switch (state) {
      case 'completed': return 'bg-green-100 text-green-800';
      case 'failed': return 'bg-red-100 text-red-800';
      case 'running': return 'bg-blue-100 text-blue-800';
      case 'pending': return 'bg-yellow-100 text-yellow-800';
      case 'cancelled': return 'bg-gray-100 text-gray-800';
      default: return 'bg-gray-100 text-gray-800';
    }
  };
  
  const getStateSymbol = (state: string): string => {
    switch (state) {
      case 'completed': return '✅';
      case 'failed': return '❌';
      case 'running': return '⚙️';
      case 'pending': return '⏳';
      case 'cancelled': return '🚫';
      default: return '❓';
    }
  };
  
  return (
    <div className="bg-white rounded-lg shadow p-6 mb-6">
      <div className="flex items-start justify-between mb-4">
        <div>
          <h1 className="text-2xl font-bold mb-2">
            Task Details
          </h1>
          <p className="text-sm font-mono text-gray-500">
            {task.id}
          </p>
        </div>
        
        <div className="flex gap-2">
          <span className={`px-3 py-1 rounded text-sm font-medium ${getStateColor(task.state)}`}>
            {getStateSymbol(task.state)} {task.state}
          </span>
          
          <span className={`px-3 py-1 rounded text-sm font-medium ${
            connectionStatus === 'connected' ? 'bg-green-100 text-green-800' :
            connectionStatus === 'connecting' ? 'bg-yellow-100 text-yellow-800' :
            'bg-red-100 text-red-800'
          }`}>
            ● {connectionStatus}
          </span>
        </div>
      </div>
      
      <div className="grid grid-cols-2 gap-4 text-sm">
        <div>
          <div className="text-gray-600 mb-1">Source</div>
          <div className="font-medium">{task.source}</div>
        </div>
        
        <div>
          <div className="text-gray-600 mb-1">Created</div>
          <div className="font-medium">
            {new Date(task.created_ts).toLocaleString()}
          </div>
        </div>
      </div>
      
      {task.payload && typeof task.payload === 'object' && 'request' in task.payload && (
        <div className="mt-4">
          <div className="text-gray-600 text-sm mb-1">Request</div>
          <div className="bg-gray-50 rounded p-3 text-sm">
            {task.payload.request}
          </div>
        </div>
      )}
    </div>
  );
}

export default function TaskDetailPageEnhanced({
  params,
}: {
  params: Promise<{ task_id: string }>;
}) {
  const { task_id } = use(params);
  
  // Fetch task metadata via REST
  const { data: task, isLoading, error } = useQuery({
    queryKey: ['task', task_id],
    queryFn: () => fetchTask(task_id),
    refetchInterval: 5000, // Refresh every 5s
  });
  
  // Stream live events via WebSocket
  const { status: connectionStatus } = useTaskEvents(task_id);
  const events = useEventBuffer(task_id, { maxSize: 500 });
  
  if (isLoading) {
    return (
      <div className="p-8">
        <div className="text-center">
          <div className="inline-block animate-spin rounded-full h-8 w-8 border-b-2 border-blue-600"></div>
          <p className="mt-4 text-gray-600">Loading task...</p>
        </div>
      </div>
    );
  }
  
  if (error || !task) {
    return (
      <div className="p-8">
        <div className="bg-red-50 border border-red-200 rounded-lg p-4">
          <h2 className="text-red-800 font-semibold mb-2">Error Loading Task</h2>
          <p className="text-red-600">
            {error instanceof Error ? error.message : 'Task not found'}
          </p>
        </div>
      </div>
    );
  }
  
  return (
    <div className="p-6 max-w-7xl mx-auto">
      {/* Breadcrumb */}
      <div className="text-sm text-gray-500 mb-6">
        <a href="/" className="hover:text-gray-700">Home</a>
        <span className="mx-2">/</span>
        <a href="/tasks" className="hover:text-gray-700">Tasks</a>
        <span className="mx-2">/</span>
        <span className="font-medium text-gray-900">{task_id.slice(0, 8)}</span>
      </div>
      
      {/* Task Header */}
      <TaskHeader task={task} connectionStatus={connectionStatus} />
      
      {/* Event Timeline */}
      <div className="bg-white rounded-lg shadow">
        <div className="p-4 border-b">
          <h2 className="text-lg font-semibold">
            Live Event Timeline
          </h2>
          <p className="text-sm text-gray-600 mt-1">
            Real-time updates via WebSocket • {events.length} events received
          </p>
        </div>
        
        <div style={{ height: 'calc(100vh - 450px)', minHeight: '400px' }}>
          <EventFeed 
            events={events}
            filterTaskId={task_id}
            autoScroll={true}
            showTimestamps={true}
          />
        </div>
      </div>
    </div>
  );
}
