/**
 * React Query hooks for memory REST endpoints.
 * All hooks use polling so the dashboard refreshes when memory changes.
 */

import { useQuery } from '@tanstack/react-query';
import {
  EpisodeSchema, FactSchema, KnowledgeDocSchema,
  KnowledgeChunkSchema, MemoryStatsSchema,
  type Episode, type Fact, type KnowledgeDoc,
  type KnowledgeChunk, type MemoryStats,
} from './contracts';
import { z } from 'zod';

const API_BASE =
  (typeof process !== 'undefined' && process.env.NEXT_PUBLIC_ATLAS_API_URL)
    ? process.env.NEXT_PUBLIC_ATLAS_API_URL
    : 'http://localhost:8000/api/v1';

async function get<T>(path: string, schema: z.ZodType<T>): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, { cache: 'no-store' });
  if (!res.ok) throw new Error(`ATLAS ${res.status}: ${await res.text().then(t => t.slice(0, 200))}`);
  const json: unknown = await res.json();
  return schema.parse(json);
}

// ---------------------------------------------------------------------------
// Stats — dashboard header cards
// ---------------------------------------------------------------------------

export function useMemoryStats() {
  return useQuery<MemoryStats>({
    queryKey: ['memory', 'stats'],
    queryFn: () => get('/memory/stats', MemoryStatsSchema),
    refetchInterval: 5000,
    staleTime: 2000,
  });
}

// ---------------------------------------------------------------------------
// Episodes
// ---------------------------------------------------------------------------

export function useEpisodes(params: {
  task_id?: string;
  kind?: string;
  min_salience?: number;
  limit?: number;
} = {}) {
  const qs = new URLSearchParams();
  if (params.task_id)     qs.set('task_id', params.task_id);
  if (params.kind)        qs.set('kind', params.kind);
  if (params.min_salience !== undefined) qs.set('min_salience', String(params.min_salience));
  if (params.limit)       qs.set('limit', String(params.limit));
  const query = qs.toString() ? `?${qs}` : '';

  return useQuery<Episode[]>({
    queryKey: ['memory', 'episodes', params],
    queryFn: () => get(`/memory/episodes${query}`, z.array(EpisodeSchema)),
    refetchInterval: 8000,
    staleTime: 3000,
  });
}

// ---------------------------------------------------------------------------
// Facts
// ---------------------------------------------------------------------------

export function useFacts(params: {
  kind?: string;
  min_confidence?: number;
  limit?: number;
} = {}) {
  const qs = new URLSearchParams();
  if (params.kind)              qs.set('kind', params.kind);
  if (params.min_confidence !== undefined) qs.set('min_confidence', String(params.min_confidence));
  if (params.limit)             qs.set('limit', String(params.limit));
  const query = qs.toString() ? `?${qs}` : '';

  return useQuery<Fact[]>({
    queryKey: ['memory', 'facts', params],
    queryFn: () => get(`/memory/facts${query}`, z.array(FactSchema)),
    refetchInterval: 10000,
    staleTime: 5000,
  });
}

// ---------------------------------------------------------------------------
// Knowledge documents
// ---------------------------------------------------------------------------

export function useKnowledgeDocs(limit = 50, offset = 0) {
  return useQuery<KnowledgeDoc[]>({
    queryKey: ['memory', 'knowledge', 'docs', limit, offset],
    queryFn: () => get(`/memory/knowledge?limit=${limit}&offset=${offset}`, z.array(KnowledgeDocSchema)),
    refetchInterval: 15000,
    staleTime: 8000,
  });
}

export function useKnowledgeSearch(query: string, limit = 10) {
  return useQuery<KnowledgeChunk[]>({
    queryKey: ['memory', 'knowledge', 'search', query, limit],
    queryFn: () => get(
      `/memory/knowledge/search?q=${encodeURIComponent(query)}&limit=${limit}`,
      z.array(KnowledgeChunkSchema),
    ),
    enabled: query.trim().length > 0,
    staleTime: 10000,
  });
}

// ---------------------------------------------------------------------------
// Preferences
// ---------------------------------------------------------------------------

export function usePreferences() {
  return useQuery<Record<string, string>>({
    queryKey: ['memory', 'preferences'],
    queryFn: async (): Promise<Record<string, string>> => {
      const res = await fetch(`${API_BASE}/memory/preferences`, { cache: 'no-store' });
      if (!res.ok) throw new Error(`ATLAS ${res.status}`);
      const json = await res.json() as Record<string, string>;
      return json;
    },
    refetchInterval: 15000,
    staleTime: 8000,
  });
}
