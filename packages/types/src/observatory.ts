/**
 * Mirrors `cortex_api.schemas.observatory` and the event payloads published by
 * `cortex_api.services.observatory.events`. Keep both sides in sync.
 */
import type { Objective, RoutingMode } from "./completion";
import type { Id, MessageRole, MemoryType, Timestamp } from "./memory";

export interface Organization {
  id: Id;
  name: string;
  slug: string;
  settings: Record<string, unknown>;
  created_at: Timestamp;
  updated_at: Timestamp;
}

// --- Overview -------------------------------------------------------------------------------------

/** A request is one completion: every attempt sharing a `completion_id`. */
export interface RequestStats {
  total: number;
  /** Requests where no attempt succeeded. */
  failed: number;
  success_rate: number | null;
  /** Latency the caller waited for: the sum of attempt latencies. */
  avg_latency_ms: number | null;
  p50_latency_ms: number | null;
  p95_latency_ms: number | null;
  tokens: number;
  cost_estimate: number;
  fallbacks: number;
  fallback_rate: number | null;
}

export interface TimelineBucket {
  start: Timestamp;
  requests: number;
  failed: number;
  avg_latency_ms: number | null;
  p95_latency_ms: number | null;
}

/** Successful attempts only: who actually answered. */
export interface ProviderShare {
  provider: string;
  requests: number;
  /** 0..1 of all successful attempts in the window. */
  share: number;
  avg_latency_ms: number;
  tokens: number;
  cost_estimate: number;
  models: string[];
}

export interface OverviewResponse {
  window_hours: number;
  generated_at: Timestamp;
  bucket_seconds: number;
  requests: RequestStats;
  /** Same-length window immediately before, for deltas. */
  previous: RequestStats;
  /** Zero-filled, oldest first; the first bucket may start before the window. */
  timeline: TimelineBucket[];
  providers: ProviderShare[];
  conversations: {
    /** Live conversations updated inside the window. */
    active: number;
    total: number;
  };
  knowledge: {
    documents: number;
    chunks: number;
    tokens: number;
    indexed_in_window: number;
    queries_in_window: number;
    avg_confidence: number | null;
    zero_result_rate: number | null;
  };
  memories: { total: number };
}

// --- Real-time events -----------------------------------------------------------------------------

export interface RequestReceivedData {
  request_id: Id;
  model: string | null;
  provider: string | null;
  routing_mode: RoutingMode | null;
  objective: Objective | null;
  messages: number;
  memory: boolean;
  knowledge: boolean;
}

export interface ExecutionCompletedData {
  request_id: Id;
  completion_id: Id;
  provider: string;
  model: string;
  objective: Objective;
  routing_mode: RoutingMode;
  routing_reason: string;
  latency_ms: number;
  prompt_tokens: number;
  completion_tokens: number;
  cost_estimate: number;
  attempts: number;
  fallback: boolean;
  grounded: boolean;
}

export interface ExecutionFailedData {
  request_id: Id;
  /** Null when the request failed before any provider was called. */
  completion_id: Id | null;
  error: string;
  attempts?: number;
}

export interface DocumentIngestingData {
  /** Correlates `document.ingesting` with its `document.indexed` or `document.failed`. */
  ingest_id: Id;
  title: string | null;
  filename: string | null;
  bytes: number;
}

export interface DocumentIndexedData extends DocumentIngestingData {
  document_id: Id;
  title: string;
  mime_type: string;
  chunk_count: number;
  embedded_chunks: number;
  token_count: number;
  embedding_space: string | null;
  ingestion_ms: number;
}

export interface DocumentFailedData extends DocumentIngestingData {
  error: string;
  error_type: string;
}

export interface DocumentDeletedData {
  document_id: Id;
  title: string;
}

export interface ConversationCreatedData {
  conversation_id: Id;
  title: string | null;
}

export interface ConversationDeletedData {
  conversation_id: Id;
}

export interface MessageAppendedData {
  conversation_id: Id;
  message_id: Id;
  role: MessageRole;
  token_count: number;
  /** Whether the hot Redis session took the message. */
  cached: boolean;
}

export interface MemoryStoredData {
  memory_id: Id;
  type: MemoryType;
  summary: string;
  importance: number;
}

export interface CortexEventDataMap {
  "request.received": RequestReceivedData;
  "execution.completed": ExecutionCompletedData;
  "execution.failed": ExecutionFailedData;
  "document.ingesting": DocumentIngestingData;
  "document.indexed": DocumentIndexedData;
  "document.failed": DocumentFailedData;
  "document.deleted": DocumentDeletedData;
  "memory.conversation_created": ConversationCreatedData;
  "memory.conversation_deleted": ConversationDeletedData;
  "memory.message_appended": MessageAppendedData;
  "memory.stored": MemoryStoredData;
}

export type CortexEventType = keyof CortexEventDataMap;

export type CortexEvent = {
  [K in CortexEventType]: {
    id: Id;
    type: K;
    organization_id: Id;
    occurred_at: Timestamp;
    data: CortexEventDataMap[K];
  };
}[CortexEventType];

/** Control frames on `/v1/events`, interleaved with `CortexEvent`s. */
export type StreamControlFrame =
  | { type: "stream.ready"; data: { organization_id: Id } }
  | { type: "stream.ping"; data: Record<string, never> };

export type StreamFrame = CortexEvent | StreamControlFrame;
