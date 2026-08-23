/**
 * React Query hooks for memory REST endpoints.
 * All hooks use polling so the dashboard refreshes when memory changes.
 *
 * Requests go through `requestContract` from the shared client, NOT a local
 * `fetch`. The local copy this replaced threw `new Error("ATLAS 404: …")` and let
 * `ZodError` escape raw, which meant the retry predicate retried 404s and the
 * error row rendered zod's own message — internal backend field names — on screen.
 */

import { useQuery } from '@tanstack/react-query';
import { requestContract } from '@/lib/api/client';
import {
  EpisodeSchema, FactSchema, KnowledgeDocSchema,
  KnowledgeChunkSchema, MemoryStatsSchema, PreferencesSchema,
  type Episode, type Fact, type KnowledgeDoc,
  type KnowledgeChunk, type MemoryStats, type Preferences,
} from './contracts';
import { z } from 'zod';

// ---------------------------------------------------------------------------
// Stats — dashboard header cards
// ---------------------------------------------------------------------------

export function useMemoryStats() {
  return useQuery<MemoryStats>({
    queryKey: ['memory', 'stats'],
    queryFn: () => requestContract('/memory/stats', MemoryStatsSchema),
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
    queryFn: () => requestContract(`/memory/episodes${query}`, z.array(EpisodeSchema)),
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
    queryFn: () => requestContract(`/memory/facts${query}`, z.array(FactSchema)),
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
    queryFn: () => requestContract(
      `/memory/knowledge?limit=${limit}&offset=${offset}`,
      z.array(KnowledgeDocSchema),
    ),
    refetchInterval: 15000,
    staleTime: 8000,
  });
}

export function useKnowledgeSearch(query: string, limit = 10) {
  return useQuery<KnowledgeChunk[]>({
    queryKey: ['memory', 'knowledge', 'search', query, limit],
    queryFn: () => requestContract(
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
  return useQuery<Preferences>({
    queryKey: ['memory', 'preferences'],
    queryFn: () => requestContract('/memory/preferences', PreferencesSchema),
    refetchInterval: 15000,
    staleTime: 8000,
  });
}
