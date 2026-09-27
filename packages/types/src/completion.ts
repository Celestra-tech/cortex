/** Mirrors `cortex_api.schemas.completion`. Keep both sides in sync. */

import type { KnowledgeOptions, KnowledgeUsage } from "./knowledge";
import type { Id, MessageRole, Timestamp } from "./memory";

export type ProviderName = "openai" | "anthropic" | "gemini" | "deepseek" | "qwen" | "llama";

export type Capability = "chat" | "vision" | "tools" | "json_mode" | "reasoning";

export type RoutingMode = "auto" | "preferred" | "strict";

export type Objective = "balanced" | "quality" | "speed" | "cost";

export type FinishReason = "stop" | "length" | "content_filter" | "tool_calls" | "other";

export interface ChatMessage {
  role: MessageRole;
  content: string;
}

export interface MemoryOptions {
  conversation_id: Id;
  /** Prepend the conversation's hot session. Send only new turns when true. Default true. */
  include_history?: boolean;
  /** Long-term memories to inject (0-20). Default 5. */
  memory_limit?: number;
  /** Append the new turns and the output to the conversation. Default true. */
  persist?: boolean;
}

export interface RoutingOptions {
  /** Defaults to `preferred` when a model or provider is given, else `auto`. */
  mode?: RoutingMode;
  provider?: ProviderName;
  capabilities?: Capability[];
  allow_fallback?: boolean;
}

export interface CompletionRequest {
  /** `provider/model`, bare model id, or alias. Omit to let Cortex choose. */
  model?: string;
  objective?: Objective;
  messages: ChatMessage[];
  temperature?: number;
  max_tokens?: number;
  memory?: MemoryOptions;
  /** Ground the answer in retrieved documents with numbered citations. */
  knowledge?: KnowledgeOptions;
  metadata?: Record<string, unknown>;
  routing?: RoutingOptions;
  /** Respond with Server-Sent Events (`start`, `token`, `complete`). SDKs set this for you. */
  stream?: boolean;
}

export interface TokenUsage {
  prompt: number;
  completion: number;
  total: number;
}

export interface CompletionAttempt {
  provider: string;
  model: string;
  success: boolean;
  latency_ms: number;
  error: string | null;
}

export interface CompletionResponse {
  id: Id;
  object: "cortex.completion";
  created_at: Timestamp;
  provider: string;
  model: string;
  output: string;
  latency_ms: number;
  tokens: TokenUsage;
  finish_reason: FinishReason;
  routing_reason: string;
  routing_mode: RoutingMode;
  /** USD, from catalog prices. */
  cost_estimate: number;
  attempts: CompletionAttempt[];
  conversation_id: Id | null;
  knowledge: KnowledgeUsage | null;
}

/** Body of a `502`: every provider failed. */
export interface CompletionErrorResponse {
  id: Id;
  detail: string;
  attempts: CompletionAttempt[];
}

export interface ModelInfo {
  /** `provider/model`; accepted as `model` in completions. */
  id: string;
  provider: ProviderName;
  model: string;
  display_name: string;
  capabilities: Capability[];
  context_window: number;
  max_output_tokens: number;
  pricing: { input_per_mtok: number; output_per_mtok: number; currency: "USD" };
  quality: number;
  expected_latency_ms: number;
  available: boolean;
  availability: "ok" | "not_configured" | "circuit_open";
  /** Permitted by this organization's routing policy. */
  allowed: boolean;
}

export interface ModelListResponse {
  models: ModelInfo[];
}

export interface ModelExecution {
  id: Id;
  organization_id: Id;
  completion_id: Id;
  attempt: number;
  provider: string;
  model: string;
  routing_mode: RoutingMode;
  is_fallback: boolean;
  latency_ms: number;
  prompt_tokens: number;
  completion_tokens: number;
  cost_estimate: number;
  success: boolean;
  finish_reason: string | null;
  error_type: string | null;
  error: string | null;
  metadata: ExecutionMetadata;
  created_at: Timestamp;
}

export interface ExecutionPromptMetadata {
  /** Messages sent to the provider, after memory history and grounding were added. */
  messages: number;
  system_messages: number;
  estimated_tokens: number;
  temperature: number | null;
  max_tokens: number | null;
}

export interface ExecutionMemoryUsage {
  conversation_id: Id;
  source: "cache" | "database";
  history_messages: number;
  history_tokens: number;
  memories_recalled: number;
  memory_ids: Id[];
  persisted: boolean;
}

/** Keys the router writes; older rows may lack the newer ones. */
export interface ExecutionMetadata {
  request_id?: Id;
  objective?: Objective;
  routing_reason?: string;
  provider_model?: string;
  provider_request_id?: string | null;
  /** The caller's own `metadata` from the completion request. */
  request?: Record<string, unknown>;
  prompt?: ExecutionPromptMetadata;
  memory?: ExecutionMemoryUsage | null;
  knowledge_query_id?: Id;
  [key: string]: unknown;
}

export interface ExecutionListResponse {
  items: ModelExecution[];
  total: number;
  limit: number;
  offset: number;
}
