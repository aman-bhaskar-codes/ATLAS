import { useQuery } from '@tanstack/react-query';
import { trustApi } from '../../lib/api/client';
import { TaskPageSchema, TaskViewSchema, ApprovalViewSchema, MemoryFactViewSchema, AuditPageSchema } from './contracts';

export function useTasks(cursor?: string, limit: number = 50) {
  return useQuery({
    queryKey: ['trust', 'tasks', cursor, limit],
    queryFn: async () => {
      const data = await trustApi.listTasks(cursor, limit);
      return TaskPageSchema.parse(data);
    },
    refetchInterval: 5000,
  });
}

export function useTask(taskId: string) {
  return useQuery({
    queryKey: ['trust', 'tasks', taskId],
    queryFn: async () => {
      const data = await trustApi.getTask(taskId);
      return TaskViewSchema.parse(data);
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
      const data = (await trustApi.pendingApprovals()) as unknown[];
      return data.map((item: unknown) => ApprovalViewSchema.parse(item));
    },
    refetchInterval: 3000,
  });
}

export function useApproval(approvalId: string) {
  return useQuery({
    queryKey: ['trust', 'approvals', approvalId],
    queryFn: async () => {
      const data = await trustApi.getApproval(approvalId);
      return ApprovalViewSchema.parse(data);
    },
  });
}

export function useMemorySearch(query: string, limit: number = 30) {
  return useQuery({
    queryKey: ['trust', 'memory', 'search', query, limit],
    queryFn: async () => {
      if (!query) return [];
      const data = (await trustApi.searchMemory(query, limit)) as unknown[];
      return data.map((item: unknown) => MemoryFactViewSchema.parse(item));
    },
    enabled: !!query,
  });
}

export function useMemoryFact(factId: string) {
  return useQuery({
    queryKey: ['trust', 'memory', 'facts', factId],
    queryFn: async () => {
      const data = await trustApi.getMemoryFact(factId);
      return MemoryFactViewSchema.parse(data);
    },
  });
}

export function useAuditLog(cursor?: string, limit: number = 50, filters?: { taskId?: string, correlationId?: string, executionId?: string }) {
  return useQuery({
    queryKey: ['trust', 'audit', cursor, limit, filters],
    queryFn: async () => {
      const data = await trustApi.getAuditLog(cursor, limit, filters);
      return AuditPageSchema.parse(data);
    },
  });
}
