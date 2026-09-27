/** Mirrors `cortex_api.schemas.memory`. Keep both sides in sync. */

/** UUID string. */
export type Id = string;
/** ISO 8601 timestamp with offset. */
export type Timestamp = string;

export type MessageRole = "system" | "user" | "assistant" | "tool";

export type MemoryType = "episodic" | "semantic" | "procedural" | "preference";

export interface ConversationCreate {
  title?: string | null;
}

export interface Conversation {
  id: Id;
  organization_id: Id;
  title: string | null;
  created_at: Timestamp;
  updated_at: Timestamp;
}

export interface ConversationSummary extends Conversation {
  message_count: number;
  /** Whether a hot Redis session exists; null when Redis was unreachable. */
  session: "hot" | "cold" | null;
}

export interface ConversationListResponse {
  items: ConversationSummary[];
  total: number;
  limit: number;
  offset: number;
}

export interface ConversationDetail extends Conversation {
  message_count: number;
  /** Most recent messages, oldest first. */
  messages: Message[];
}

export interface MessageCreate {
  conversation_id: Id;
  role: MessageRole;
  content: string;
  metadata?: Record<string, unknown>;
}

export interface Message {
  id: Id;
  conversation_id: Id;
  role: MessageRole;
  content: string;
  token_count: number;
  metadata: Record<string, unknown>;
  created_at: Timestamp;
}

export interface SessionMessage {
  id: Id;
  role: MessageRole;
  content: string;
  token_count: number;
  created_at: Timestamp;
}

export interface MemoryCreate {
  type: MemoryType;
  content: string;
  /** Derived from `content` when omitted. */
  summary?: string | null;
  /** 0..1, defaults to 0.5. */
  importance?: number;
  source_message_id?: Id | null;
}

export interface Memory {
  id: Id;
  organization_id: Id;
  type: MemoryType;
  summary: string;
  content: string;
  importance: number;
  source_message_id: Id | null;
  created_at: Timestamp;
  updated_at: Timestamp;
}

export interface RankedMemory extends Memory {
  /** Relevance * importance * recency. Comparable within one response only. */
  score: number;
}

export interface MemorySearchResponse {
  query: string | null;
  memories: RankedMemory[];
}

export interface ConversationContext {
  conversation_id: Id;
  messages: SessionMessage[];
  token_count: number;
  last_activity: Timestamp;
  source: "cache" | "database";
  memories: RankedMemory[];
}
