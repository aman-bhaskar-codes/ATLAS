"use client";
// frontend/features/runtime-console/useCancelTask.ts
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useCallback, useRef } from "react";
import { atlasApi } from "../../lib/api/client";

export function useCancelTask(taskId: string) {
  const queryClient = useQueryClient();
  
  // Idempotency key must survive re-renders but be unique per page load/task
  const idempotencyKey = useRef(crypto.randomUUID());

  const { mutate, isPending, error, data } = useMutation({
    mutationFn: () =>
      atlasApi.cancelTask(taskId, {
        idempotency_key: idempotencyKey.current,
        reason: "user_requested",
      }),
    onSuccess: () => {
      // Invalidate the task snapshot to fetch the new 'cancelling' state
      queryClient.invalidateQueries({ queryKey: ["task", taskId] });
    },
  });

  const cancelTask = useCallback(() => {
    mutate();
  }, [mutate]);

  return { cancelTask, isCancelling: isPending, error, result: data };
}
