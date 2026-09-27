/** Mirrors `cortex_api.schemas.knowledge`. Keep both sides in sync. */

import type { Id, Timestamp } from "./memory";

export type SearchMode = "hybrid" | "vector" | "keyword";

export type ConfidenceLevel = "high" | "medium" | "low" | "none";

export type TextMimeType = "text/plain" | "text/markdown";

export interface ChunkingOptions {
  /** Tokens per chunk (32-8192). */
  chunk_size?: number;
  /** Tokens repeated from the previous chunk; must be below `chunk_size`. */
  chunk_overlap?: number;
  /** Split points, tried in order. */
  separators?: string[];
}

export interface DocumentCreate {
  title: string;
  content: string;
  source?: string;
  mime_type?: TextMimeType;
  metadata?: Record<string, unknown>;
  chunking?: ChunkingOptions;
}

export interface IngestionStats {
  ingestion_ms: number;
  embedding_ms: number;
  /** Per-stage timings and counts: extract_ms, chunk_ms, embed_ms, store_ms, pages, … */
  stages: Record<string, unknown>;
}

export interface KnowledgeDocument {
  id: Id;
  title: string;
  source: string | null;
  mime_type: string;
  metadata: Record<string, unknown>;
  content_hash: string;
  byte_size: number;
  char_count: number;
  token_count: number;
  chunk_count: number;
  /** `provider/model@dims`; null when nothing could be embedded. */
  embedding_space: string | null;
  ingestion: IngestionStats;
  created_at: Timestamp;
}

export interface DocumentChunk {
  id: Id;
  chunk_index: number;
  content: string;
  token_count: number;
  section: string | null;
  page_start: number | null;
  page_end: number | null;
  char_start: number;
  char_end: number;
  embedding_space: string | null;
}

export interface DocumentDetail extends KnowledgeDocument {
  /** Present when requested with `include_chunks`. */
  chunks: DocumentChunk[] | null;
}

export interface DocumentListResponse {
  items: KnowledgeDocument[];
  total: number;
  limit: number;
  offset: number;
}

/** Body of a `409`: identical content was already ingested. */
export interface DuplicateDocumentError {
  detail: string;
  document_id: Id;
}

export interface SearchFilters {
  document_ids?: Id[];
  sources?: string[];
  mime_types?: string[];
  /** Containment: `{ team: "billing" }` keeps documents whose metadata includes it. */
  metadata?: Record<string, unknown>;
  created_after?: Timestamp;
  created_before?: Timestamp;
}

export interface KnowledgeSearchRequest {
  query: string;
  top_k?: number;
  mode?: SearchMode;
  filters?: SearchFilters;
  /** 0 disables the recency boost; 1 lets age dominate. */
  recency_weight?: number;
  max_context_tokens?: number;
}

export interface Citation {
  index: number;
  document_id: Id;
  chunk_ids: Id[];
  title: string;
  source: string | null;
  section: string | null;
  page_start: number | null;
  page_end: number | null;
  /** `Title > Section (p. 3)` */
  label: string;
  score: number;
  snippet: string;
  /** On completions: whether the answer referenced this citation. */
  cited: boolean | null;
}

export interface SearchHit {
  chunk_id: Id;
  document_id: Id;
  title: string;
  source: string | null;
  section: string | null;
  chunk_index: number;
  page_start: number | null;
  page_end: number | null;
  content: string;
  score: number;
  vector_similarity: number | null;
  keyword_score: number | null;
  vector_rank: number | null;
  keyword_rank: number | null;
  recency: number;
  /** Citation number when the chunk made it into the context. */
  citation: number | null;
}

export interface Confidence {
  score: number;
  level: ConfidenceLevel;
  similarity: number | null;
  coverage: number | null;
  agreement: number | null;
}

export interface AssembledContext {
  /** Numbered sources, ready for a system message. */
  text: string;
  token_count: number;
  truncated: boolean;
  citations: Citation[];
  confidence: Confidence;
}

export interface SearchMetrics {
  latency_ms: number;
  embedding_ms: number;
  vector_ms: number;
  keyword_ms: number;
  vector_candidates: number;
  keyword_candidates: number;
  query_embedding_cached: boolean;
  embedding_space: string | null;
  query_terms: string[];
  warnings: string[];
}

export interface KnowledgeSearchResponse {
  query_id: Id;
  query: string;
  mode: SearchMode;
  results: SearchHit[];
  context: AssembledContext;
  metrics: SearchMetrics;
}

/** `knowledge` option on completions. */
export interface KnowledgeOptions {
  top_k?: number;
  mode?: SearchMode;
  filters?: SearchFilters;
  max_context_tokens?: number;
  /** Below this, the context is withheld from the model. */
  min_confidence?: number;
  /** Defaults to the last user message. */
  query?: string;
}

export interface KnowledgeUsage {
  query_id: Id;
  /** False when nothing was retrieved or confidence was below `min_confidence`. */
  applied: boolean;
  confidence: Confidence;
  citations: Citation[];
  /** Citation numbers the answer referenced, in order of first use. */
  cited: number[];
}

export interface CorpusMetrics {
  documents: number;
  chunks: number;
  tokens: number;
  /** Documents ingested within the window. */
  ingested: number;
  avg_ingestion_ms: number | null;
  p95_ingestion_ms: number | null;
  avg_embedding_ms: number | null;
}

export interface RetrievalMetrics {
  queries: number;
  p50_ms: number | null;
  p95_ms: number | null;
  avg_embedding_ms: number | null;
  avg_confidence: number | null;
  avg_coverage: number | null;
  avg_agreement: number | null;
  zero_result_rate: number | null;
  cache_hit_rate: number | null;
  grounded_completions: number;
  citations_offered: number;
  citations_used: number;
  citation_usage_rate: number | null;
}

export interface KnowledgeMetricsResponse {
  window_hours: number;
  corpus: CorpusMetrics;
  retrieval: RetrievalMetrics;
}

// --- Query log ------------------------------------------------------------------------------------

/** One logged retrieval (`GET /v1/knowledge/queries`). */
export interface KnowledgeQueryLog {
  id: Id;
  query: string;
  mode: SearchMode;
  top_k: number;
  filters: Record<string, unknown>;
  embedding_space: string | null;
  result_count: number;
  vector_candidates: number;
  keyword_candidates: number;
  citation_count: number;
  context_tokens: number;
  latency_ms: number;
  embedding_ms: number;
  vector_ms: number;
  keyword_ms: number;
  query_embedding_cached: boolean;
  top_score: number | null;
  confidence: number;
  coverage: number | null;
  agreement: number | null;
  /** Set when the retrieval grounded a completion. */
  completion_id: Id | null;
  /** Citation numbers the answer referenced; null until known. */
  cited: number[] | null;
  created_at: Timestamp;
}

export interface KnowledgeQueryResult {
  chunk_id: Id;
  document_id: Id;
  score: number;
  /** Citation number when the chunk made it into the assembled context. */
  citation: number | null;
}

export interface KnowledgeQueryDetail extends KnowledgeQueryLog {
  results: KnowledgeQueryResult[];
}

export interface KnowledgeQueryListResponse {
  items: KnowledgeQueryLog[];
  total: number;
  limit: number;
  offset: number;
}
