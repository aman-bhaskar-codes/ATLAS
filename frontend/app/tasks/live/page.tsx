"use client";

import React, { useEffect } from 'react';
import { useQuery } from '@tanstack/react-query';
import { atlasApi } from '@/lib/api/client';
import { CommandWorkspace } from '@/components/workspace/CommandWorkspace';
import { useRouter } from 'next/navigation';
import { Loader2 } from 'lucide-react';
import { ErrorState } from '@/components/primitives/ErrorState';

export default function LiveRunPage() {
  const router = useRouter();
  
  // Fetch recent tasks to see if one is currently active
  const { data: tasks, isLoading, isError, error } = useQuery({
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
        <ErrorState title="Failed to check live tasks" error={error} />
      </div>
    );
  }

  // Pristine empty state
  return (
    <div className="flex flex-col justify-center min-h-[60vh] max-w-4xl mx-auto w-full pt-12">
      <div className="mb-8 text-center">
        <h1 className="text-2xl font-medium text-[var(--paper-100)] mb-2">Live Run</h1>
        <p className="text-[var(--paper-500)]">Start a new task to view its live execution trace.</p>
      </div>
      <CommandWorkspace />
    </div>
  );
}
