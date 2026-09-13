import { z } from 'zod';

export const KnowledgeDocumentSchema = z.object({
  id: z.string(),
  title: z.string(),
  source_path: z.string(),
  source_type: z.string(),
  chunk_count: z.number(),
  indexed: z.boolean(),
  created_ts: z.string(),
});
export type KnowledgeDocument = z.infer<typeof KnowledgeDocumentSchema>;

export const KnowledgeDocumentListSchema = z.object({
  documents: z.array(KnowledgeDocumentSchema),
  total: z.number(),
});

export const SearchResultSchema = z.object({
  chunk_id: z.string(),
  document_id: z.string(),
  document_title: z.string(),
  content: z.string(),
  score: z.number(),
  chunk_index: z.number(),
  total_chunks: z.number(),
  source_path: z.string(),
  source_type: z.string(),
});
export type SearchResult = z.infer<typeof SearchResultSchema>;

export const SearchResponseSchema = z.object({
  query: z.string(),
  results: z.array(SearchResultSchema),
  total: z.number(),
});

export const IngestResponseSchema = z.object({
  document_id: z.string(),
  title: z.string(),
  chunks: z.number(),
  indexed: z.boolean(),
});
export type IngestResponse = z.infer<typeof IngestResponseSchema>;
