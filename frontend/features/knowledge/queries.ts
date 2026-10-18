import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { requestContract } from '@/lib/api/client';
import {
  KnowledgeDocumentListSchema,
  SearchResponseSchema,
  IngestResponseSchema
} from './contracts';
import { z } from 'zod';

export function useKnowledgeDocuments(limit = 50, offset = 0) {
  return useQuery({
    queryKey: ['knowledge', 'documents', limit, offset],
    queryFn: () => requestContract(
      `/knowledge/documents?limit=${limit}&offset=${offset}`,
      KnowledgeDocumentListSchema,
    ),
    refetchInterval: 5000,
  });
}

export function useKnowledgeSearch(query: string, limit = 10) {
  return useQuery({
    queryKey: ['knowledge', 'search', query, limit],
    queryFn: () => requestContract(
      `/knowledge/search`,
      SearchResponseSchema,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ query, limit })
      }
    ),
    enabled: query.trim().length > 0,
  });
}

export function useIngestDocument() {
  const queryClient = useQueryClient();
  
  return useMutation({
    mutationFn: (data: { source_path: string; source_type: string; title?: string }) => {
      return requestContract(
        '/knowledge/ingest',
        IngestResponseSchema,
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(data)
        }
      );
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['knowledge', 'documents'] });
    }
  });
}

export function useDeleteDocument() {
  const queryClient = useQueryClient();
  
  return useMutation({
    mutationFn: (documentId: string) => {
      return requestContract(
        `/knowledge/documents/${documentId}`,
        z.object({ status: z.string(), document_id: z.string() }),
        { method: 'DELETE' }
      );
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['knowledge', 'documents'] });
    }
  });
}

export function useReindexCorpus() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => requestContract('/knowledge/reindex', z.object({
      documents: z.number().int().nonnegative(),
      embedded: z.number().int().nonnegative(),
      errors: z.number().int().nonnegative(),
      timed_out: z.number().int().min(0).max(1),
    }), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ limit_documents: 10 }),
    }, 35_000),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['knowledge'] }),
    retry: false,
  });
}
