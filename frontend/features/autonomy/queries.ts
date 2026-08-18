import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { autonomyApi } from '../../lib/api/client';
import { Automation } from '../../lib/api/contracts';

export function useAutomations(enabledOnly: boolean = false) {
  return useQuery({
    queryKey: ['autonomy', 'automations', enabledOnly],
    queryFn: async () => {
      return await autonomyApi.listAutomations(enabledOnly);
    },
    refetchInterval: 5000,
  });
}

export function useAutomation(id: string) {
  return useQuery({
    queryKey: ['autonomy', 'automations', id],
    queryFn: async () => {
      return await autonomyApi.getAutomation(id);
    },
  });
}

export function useCreateAutomation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (auto: Partial<Automation>) => {
      return await autonomyApi.createAutomation(auto);
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['autonomy', 'automations'] });
    },
  });
}

export function useUpdateAutomation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({ id, auto }: { id: string, auto: Partial<Automation> }) => {
      return await autonomyApi.updateAutomation(id, auto);
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['autonomy', 'automations'] });
    },
  });
}

export function useToggleAutomation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({ id, enabled, auto }: { id: string, enabled: boolean, auto: Partial<Automation> }) => {
      return await autonomyApi.updateAutomation(id, { ...auto, enabled });
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['autonomy', 'automations'] });
    },
  });
}

export function useDeleteAutomation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (id: string) => {
      return await autonomyApi.deleteAutomation(id);
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['autonomy', 'automations'] });
    },
  });
}
