/**
 * Tasks List Page
 * Shows all recent tasks with links to detail pages
 */

'use client';

import { useQuery } from '@tanstack/react-query';
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
  
  if (!response.ok) {
    throw new Error('Failed to fetch tasks');
  }
  
  return response.json();
}

function TaskRow({ task }: { task: Task }) {
  const getStateColor = (state: string): string => {
    switch (state) {
      case 'completed': return 'bg-green-100 text-green-800';
      case 'failed': return 'bg-red-100 text-red-800';
      case 'running': return 'bg-blue-100 text-blue-800';
      case 'pending': return 'bg-yellow-100 text-yellow-800';
      default: return 'bg-gray-100 text-gray-800';
    }
  };
  
  const request = task.payload?.request || 'No request details';
  
  return (
    <Link 
      href={`/tasks/${task.id}`}
      className="block border-b hover:bg-gray-50 transition-colors"
    >
      <div className="p-4">
        <div className="flex items-center justify-between mb-2">
          <div className="flex items-center gap-3">
            <span className={`px-2 py-1 rounded text-xs font-medium ${getStateColor(task.state)}`}>
              {task.state}
            </span>
            <span className="text-sm font-mono text-gray-500">
              {task.id.slice(0, 8)}
            </span>
          </div>
          
          <span className="text-xs text-gray-500">
            {new Date(task.created_ts).toLocaleString()}
          </span>
        </div>
        
        <p className="text-sm text-gray-700 truncate">
          {request}
        </p>
      </div>
    </Link>
  );
}

export default function TasksListPage() {
  const { data: tasks, isLoading, error } = useQuery({
    queryKey: ['tasks'],
    queryFn: fetchTasks,
    refetchInterval: 5000,
  });
  
  return (
    <div className="p-6 max-w-7xl mx-auto">
      <div className="mb-6">
        <h1 className="text-3xl font-bold mb-2">Tasks</h1>
        <p className="text-gray-600">View and monitor all ATLAS tasks</p>
      </div>
      
      <div className="bg-white rounded-lg shadow overflow-hidden">
        {isLoading && (
          <div className="p-8 text-center">
            <div className="inline-block animate-spin rounded-full h-8 w-8 border-b-2 border-blue-600"></div>
            <p className="mt-4 text-gray-600">Loading tasks...</p>
          </div>
        )}
        
        {error && (
          <div className="p-8">
            <div className="bg-red-50 border border-red-200 rounded p-4">
              <p className="text-red-600">
                {error instanceof Error ? error.message : 'Failed to load tasks'}
              </p>
            </div>
          </div>
        )}
        
        {tasks && tasks.length === 0 && (
          <div className="p-8 text-center text-gray-500">
            <div className="text-4xl mb-2">📋</div>
            <p>No tasks yet</p>
          </div>
        )}
        
        {tasks && tasks.length > 0 && (
          <div>
            {tasks.map((task) => (
              <TaskRow key={task.id} task={task} />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
