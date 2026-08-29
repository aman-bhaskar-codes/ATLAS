/**
 * React Query hooks for the research workspace (R9).
 *
 * A research query is NOT a second execution engine — it is an ordinary task
 * submitted through the SAME `/tasks` API that the command centre uses, so it
 * travels the identical orchestrator → ReasoningLoop → ToolDispatcher → SafetyEngine
 * funnel and returns a `Task` whose `answer` carries the cited synthesis. The
 * workspace only adds a citation-grounding *view* on top of that answer; it invents
 * no new backend surface.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { atlasApi } from "@/lib/api/client";
import { isTerminal, type Task } from "@/lib/api/contracts";

/** Submit a research question as a task. Returns the created (pending) Task. */
export function useCreateResearch() {
  const queryClient = useQueryClient();
  return useMutation<Task, unknown, string>({
    mutationFn: (question: string) =>
      atlasApi.createTask({ request: question, idempotency_key: crypto.randomUUID() }),
    onSuccess: () => {
      // The new task also belongs in the shared task history.
      void queryClient.invalidateQueries({ queryKey: ["tasks"] });
    },
    // No onError: the page renders the mutation error inline — the only place a
    // non-retried mutation failure can surface.
  });
}

/**
 * Poll a single task until it reaches a terminal state, then stop. Enabled only
 * once an id exists, so it is inert before the first submission.
 */
export function useResearchTask(taskId: string | null) {
  return useQuery<Task>({
    queryKey: ["research", "task", taskId],
    queryFn: () => atlasApi.task(taskId as string),
    enabled: taskId !== null,
    // Stop hammering the backend the moment the answer is final.
    refetchInterval: (query) => {
      const state = query.state.data?.state;
      return state && isTerminal(state) ? false : 1500;
    },
    staleTime: 1000,
  });
}
