/**
 * React Query hooks for the Trust Center views.
 *
 * Validation goes through `parseContract`, NOT `Schema.parse`. A bare `.parse`
 * throws zod's own `ZodError`, which is untyped as far as the retry predicate is
 * concerned (so a permanent shape mismatch gets retried) and whose `message` is a
 * JSON blob naming internal backend fields if any error row renders it.
 * `parseContract` converts it into `AtlasContractError` carrying the endpoint path.
 */
import { useQuery } from '@tanstack/react-query';
import { z } from 'zod';
import { parseContract, trustApi } from '../../lib/api/client';
import { TaskPageSchema, TaskViewSchema, ApprovalViewSchema, MemoryFactViewSchema, AuditPageSchema } from './contracts';

export function useTasks(cursor?: string, limit: number = 50) {
  return useQuery({
    queryKey: ['trust', 'tasks', cursor, limit],
    queryFn: async () => {
      const data = await trustApi.listTasks(cursor, limit);
      return parseContract('/tasks', TaskPageSchema, data);
    },
    refetchInterval: 5000,
  });
}

export function useTask(taskId: string) {
  return useQuery({
    queryKey: ['trust', 'tasks', taskId],
    queryFn: async () => {
      const data = await trustApi.getTask(taskId);
      return parseContract(`/tasks/${taskId}`, TaskViewSchema, data);
    },
    refetchInterval: (query) => {
      const data = query.state.data;
      if (data && ['completed', 'failed', 'cancelled'].includes(data.state)) {
        return false;
      }
      return 2000;
    },
  });
}

export function usePendingApprovals() {
  return useQuery({
    queryKey: ['trust', 'approvals', 'pending'],
    queryFn: async () => {
      const data = await trustApi.pendingApprovals();
      return parseContract('/approvals/pending', z.array(ApprovalViewSchema), data);
    },
    refetchInterval: 3000,
  });
}

export function useApproval(approvalId: string) {
  return useQuery({
    queryKey: ['trust', 'approvals', approvalId],
    queryFn: async () => {
      const data = await trustApi.getApproval(approvalId);
      return parseContract(`/approvals/${approvalId}`, ApprovalViewSchema, data);
    },
  });
}

export function useMemorySearch(query: string, limit: number = 30) {
  return useQuery({
    queryKey: ['trust', 'memory', 'search', query, limit],
    queryFn: async () => {
      if (!query) return [];
      const data = await trustApi.searchMemory(query, limit);
      return parseContract('/memory/search', z.array(MemoryFactViewSchema), data);
    },
    enabled: !!query,
  });
}

export function useMemoryFact(factId: string) {
  return useQuery({
    queryKey: ['trust', 'memory', 'facts', factId],
    queryFn: async () => {
      const data = await trustApi.getMemoryFact(factId);
      return parseContract(`/memory/facts/${factId}`, MemoryFactViewSchema, data);
    },
  });
}

export function useAuditLog(cursor?: string, limit: number = 50, filters?: { taskId?: string, correlationId?: string, executionId?: string }) {
  return useQuery({
    queryKey: ['trust', 'audit', cursor, limit, filters],
    queryFn: async () => {
      const data = await trustApi.getAuditLog(cursor, limit, filters);
      return parseContract('/audit', AuditPageSchema, data);
    },
  });
}
