/**
 * Dashboard Overview Page
 * 
 * System-wide observability with live metrics and event streams
 */

'use client';

import { useState, useEffect } from 'react';
import { useQuery } from '@tanstack/react-query';
import { useGlobalEvents } from '../../lib/websocket';
import Link from 'next/link';

interface Task {
  id: string;
  state: string;
  source: string;
  created_ts: string;
  payload?: any;
}

async function fetchTasks(): Promise<Task[]> {
  const baseUrl = process.env.NEXT_PUBLIC_ATLAS_API_URL || 'http://localhost:8000';
  const response = await fetch(`${baseUrl}/api/v1/tasks`);
  if (!response.ok) throw new Error('Failed to fetch tasks');
  return response.json();
}

function MetricCard({ title, value, subtitle, color = 'blue' }: any) {
  const colors = {
    blue: 'bg-blue-50 border-blue-200',
    green: 'bg-green-50 border-green-200',
    yellow: 'bg-yellow-50 border-yellow-200',
    purple: 'bg-purple-50 border-purple-200',
  };
  
  return (
    <div className={`border rounded-lg p-4 ${colors[color as keyof typeof colors]}`}>
      <div className="text-sm font-medium opacity-75 mb-1">{title}</div>
      <div className="text-3xl font-bold mb-1">{value}</div>
      {subtitle && <div className="text-xs opacity-75">{subtitle}</div>}
    </div>
  );
}

export default function DashboardPage() {
  const [metrics, setMetrics] = useState({
    totalEvents: 0,
    eventRate: 0,
    eventTypes: new Set<string>(),
    activeTasks: new Set<string>(),
    recentEvents: [] as any[],
    startTime: new Date(),
  });
  
  const { data: tasks = [] } = useQuery({
    queryKey: ['tasks'],
    queryFn: fetchTasks,
    refetchInterval: 5000,
  });
  
  const { data: latestEvent, status } = useGlobalEvents();
  
  useEffect(() => {
    if (!latestEvent) return;
    
    setMetrics(prev => {
      const newTypes = new Set(prev.eventTypes);
      newTypes.add(latestEvent.kind);
      
      const newTasks = new Set(prev.activeTasks);
      if (latestEvent.task_id) newTasks.add(latestEvent.task_id);
      
      const newTotal = prev.totalEvents + 1;
      const elapsed = (new Date().getTime() - prev.startTime.getTime()) / 1000;
      const rate = elapsed > 0 ? newTotal / elapsed : 0;
      
      return {
        totalEvents: newTotal,
        eventRate: rate,
        eventTypes: newTypes,
        activeTasks: newTasks,
        recentEvents: [...prev.recentEvents, latestEvent].slice(-20),
        startTime: prev.startTime,
      };
    });
  }, [latestEvent]);
  
  const activeTasks = tasks.filter(t => t.state === 'running' || t.state === 'pending');
  
  return (
    <div className="p-6 max-w-7xl mx-auto">
      <div className="mb-6">
        <h1 className="text-3xl font-bold mb-2">Dashboard</h1>
        <div className="flex items-center gap-4">
          <p className="text-gray-600">System overview</p>
          <span className={`px-3 py-1 rounded text-sm ${
            status === 'connected' ? 'bg-green-100 text-green-800' :
            'bg-yellow-100 text-yellow-800'
          }`}>
            ● {status}
          </span>
          <a 
            href="/events/search" 
            className="ml-auto px-4 py-2 bg-blue-600 text-white rounded hover:bg-blue-700 transition-colors text-sm font-medium"
          >
            🔍 Search Events
          </a>
        </div>
      </div>
      
      <div className="grid grid-cols-4 gap-4 mb-6">
        <MetricCard title="Events" value={metrics.totalEvents} color="blue" />
        <MetricCard title="Rate" value={metrics.eventRate.toFixed(1)} subtitle="events/sec" color="purple" />
        <MetricCard title="Active Tasks" value={activeTasks.length} color="green" />
        <MetricCard title="Event Types" value={metrics.eventTypes.size} color="yellow" />
      </div>
      
      <div className="grid grid-cols-2 gap-6">
        <div className="bg-white rounded-lg shadow p-6">
          <h2 className="text-lg font-semibold mb-4">Active Tasks</h2>
          {activeTasks.length === 0 ? (
            <div className="text-center py-8 text-gray-500">No active tasks</div>
          ) : (
            <div className="space-y-2">
              {activeTasks.slice(0, 5).map(task => (
                <Link key={task.id} href={`/tasks/${task.id}`} className="block p-3 bg-gray-50 rounded hover:bg-gray-100">
                  <div className="flex justify-between items-center">
                    <span className="font-mono text-sm">{task.id.slice(0, 8)}</span>
                    <span className="text-xs bg-blue-100 text-blue-800 px-2 py-1 rounded">{task.state}</span>
                  </div>
                </Link>
              ))}
            </div>
          )}
        </div>
        
        <div className="bg-white rounded-lg shadow p-6">
          <h2 className="text-lg font-semibold mb-4">Recent Events</h2>
          <div className="space-y-2 max-h-96 overflow-y-auto">
            {metrics.recentEvents.slice(-10).reverse().map((evt, i) => (
              <div key={i} className="p-2 bg-gray-50 rounded text-sm">
                <span className="font-mono">{evt.kind}</span>
                {evt.task_id && (
                  <span className="ml-2 text-xs text-gray-500">{evt.task_id.slice(0, 8)}</span>
                )}
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}
