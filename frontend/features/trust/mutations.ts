import { useMutation, useQueryClient } from '@tanstack/react-query';
import { trustApi } from '../../lib/api/client';
import { ApprovalViewSchema, MemoryMutationReceiptSchema } from './contracts';

export function useDecideApproval() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: async ({ approvalId, decision, idempotencyKey, requestId }: { approvalId: string; decision: 'approve' | 'deny'; idempotencyKey: string; requestId: string }) => {
      const data = await trustApi.decideApproval(approvalId, decision, idempotencyKey, requestId);
      return ApprovalViewSchema.parse(data);
    },
    onSuccess: (data) => {
      queryClient.invalidateQueries({ queryKey: ['trust', 'approvals', 'pending'] });
      queryClient.setQueryData(['trust', 'approvals', data.id], data);
    },
  });
}

export function useCorrectMemory() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: async ({ factId, replacementText, idempotencyKey, requestId }: { factId: string; replacementText: string; idempotencyKey: string; requestId: string }) => {
      const data = await trustApi.correctMemory(factId, replacementText, idempotencyKey, requestId);
      return MemoryMutationReceiptSchema.parse(data);
    },
    onSuccess: (data) => {
      queryClient.invalidateQueries({ queryKey: ['trust', 'memory', 'search'] });
      queryClient.setQueryData(['trust', 'memory', 'facts', data.fact.id], data.fact);
    },
  });
}
