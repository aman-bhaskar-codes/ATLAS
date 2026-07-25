"use client";
// frontend/features/runtime-console/useTaskRuntime.ts
import { useQuery } from "@tanstack/react-query";
import { atlasApi } from "../../lib/api/client";
import { isTerminal, type Task } from "../../lib/api/contracts";

export function useTaskRuntime(taskId: string) {
  // 1. Task Snapshot — authoritative baseline for elapsed time & terminal state
  const { data: task, isLoading: isTaskLoading, error: taskError, refetch: refetchTask } = useQuery<Task>({
    queryKey: ["task", taskId],
    queryFn: () => atlasApi.task(taskId),
    // Poll the task snapshot every 2 seconds until it reaches a terminal state.
    // The SSE stream handles the fine-grained events, but this ensures we don't
    // miss the authoritative DB state change.
    refetchInterval: (query) => {
      if (query.state.data && isTerminal(query.state.data.state)) {
        return false;
      }
      return 2000;
    },
    retry: 2,
  });

  return { task, isTaskLoading, taskError, refetchTask };
}
