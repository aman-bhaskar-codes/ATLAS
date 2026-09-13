"use client";

import React, { useEffect } from 'react';
import { useQuery } from '@tanstack/react-query';
import { atlasApi } from '@/lib/api/client';
import { useRouter } from 'next/navigation';
import { Loader2 } from 'lucide-react';
import { ErrorState } from '@/components/primitives/ErrorState';

export default function LiveRunPage() {
  const router = useRouter();
  
  // Fetch recent tasks to see if one is currently active
  const { data: tasks, isLoading, isError, error, refetch } = useQuery({
    queryKey: ["tasks", "recent"],
    queryFn: () => atlasApi.tasks(),
    refetchInterval: 3000,
  });

  const activeTask = tasks?.find(t => t.state !== 'completed' && t.state !== 'failed' && t.state !== 'cancelled');

  useEffect(() => {
    if (activeTask) {
      router.replace(`/tasks/${activeTask.id}`);
    }
  }, [activeTask, router]);

  if (isLoading) {
    return (
      <div className="flex items-center justify-center min-h-[50vh]">
        <Loader2 className="w-8 h-8 animate-spin text-[var(--paper-500)]" />
      </div>
    );
  }

  if (isError) {
    return (
      <div className="mt-8">
        <ErrorState title="Failed to check live tasks" error={error} onRetry={() => void refetch()} />
      </div>
    );
  }

  // Pristine empty state
  return (
    <div className="flex flex-col items-center justify-center min-h-[60vh] max-w-2xl mx-auto w-full pt-12 text-center">
      <div className="bg-ink-900 border border-ink-800 rounded-lg p-8 w-full shadow-lg">
        <h1 className="text-2xl font-medium text-[var(--paper-100)] mb-3">No Active Live Run</h1>
        <p className="text-[var(--paper-500)] mb-6 text-sm">
          There are currently no tasks executing. Start a new task from the Command Center to view its live execution trace here.
        </p>
        <button 
          onClick={() => router.push('/')}
          className="primary px-5 py-2.5 text-sm font-medium rounded shadow-sm hover:opacity-90 transition-opacity"
        >
          Go to Command Center
        </button>
      </div>
    </div>
  );
}
