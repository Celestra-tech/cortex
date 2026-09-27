/**
 * Runtime schemas. Inputs are checked before anything is sent, so mistakes
 * fail fast with a `ValidationError` naming the field. Responses are checked
 * for shape but tolerate additive API changes: unknown fields pass through and
 * enum-like fields accept new values.
 *
 * Static types live in `@celestra/cortex-types` and are re-exported here.
 */
import type {
  ChatMessage,
  CompletionRequest,
  CompletionResponse,
  Conversation,
  ConversationCreate,
  ConversationDetail,
  ConversationListResponse,
  DocumentCreate,
  DocumentListResponse,
  KnowledgeDocument,
  KnowledgeSearchRequest,
  KnowledgeSearchResponse,
  Memory,
  MemoryCreate,
  Message,
  MessageCreate,
  ModelListResponse,
} from "@celestra/cortex-types";
import { z } from "zod";

import { ValidationError, type ValidationIssue } from "./errors";

export type * from "@celestra/cortex-types";

export function issuesFromZod(error: z.ZodError): ValidationIssue[] {
  return error.issues.map((issue) => ({
    path: issue.path.map(String).join("."),
    message: issue.message,
  }));
}

/** Throws a client-side `ValidationError` (status `null`) when `value` is invalid. */
export function validateInput<T>(operation: string, schema: z.ZodType, value: T): T {
  const parsed = schema.safeParse(value);
  if (parsed.success) return value;
  const issues = issuesFromZod(parsed.error);
  throw new ValidationError(
    `${operation}: invalid input: ${issues.map((i) => `${i.path || "input"}: ${i.message}`).join("; ")}`,
    { status: null, issues },
  );
}

/** A response schema, typed as the documented API shape it checks. */
function response<T>(schema: z.ZodType): z.ZodType<T> {
  return schema as z.ZodType<T>;
}

// --- Inputs ---------------------------------------------------------------------------------------

const id = z.string().min(1);
const role = z.enum(["system", "user", "assistant", "tool"]);
const metadata = z.record(z.string(), z.unknown());

export const chatMessageSchema = z.looseObject({
  role,
  content: z.string().min(1).max(100_000),
}) satisfies z.ZodType<ChatMessage>;

const searchFilters = z.looseObject({
  document_ids: z.array(id).max(100).optional(),
  sources: z.array(z.string()).max(50).optional(),
  mime_types: z.array(z.string()).max(10).optional(),
  metadata: metadata.optional(),
  created_after: z.string().optional(),
  created_before: z.string().optional(),
});

const searchMode = z.enum(["hybrid", "vector", "keyword"]);

export const completionRequestSchema = z
  .looseObject({
    model: z.string().min(1).max(128).optional(),
    objective: z.enum(["balanced", "quality", "speed", "cost"]).optional(),
    messages: z.array(chatMessageSchema).min(1).max(500),
    temperature: z.number().min(0).max(2).optional(),
    max_tokens: z.number().int().min(1).max(200_000).optional(),
    memory: z
      .looseObject({
        conversation_id: id,
        include_history: z.boolean().optional(),
        memory_limit: z.number().int().min(0).max(20).optional(),
        persist: z.boolean().optional(),
      })
      .optional(),
    knowledge: z
      .looseObject({
        top_k: z.number().int().min(1).max(50).optional(),
        mode: searchMode.optional(),
        filters: searchFilters.optional(),
        max_context_tokens: z.number().int().min(100).max(200_000).optional(),
        min_confidence: z.number().min(0).max(1).optional(),
        query: z.string().max(2000).optional(),
      })
      .optional(),
    metadata: metadata.optional(),
    routing: z
      .looseObject({
        mode: z.enum(["auto", "preferred", "strict"]).optional(),
        provider: z.enum(["openai", "anthropic", "gemini", "deepseek", "qwen", "llama"]).optional(),
        capabilities: z
          .array(z.enum(["chat", "vision", "tools", "json_mode", "reasoning"]))
          .optional(),
        allow_fallback: z.boolean().optional(),
      })
      .optional(),
  })
  .refine(
    (request) => !request.messages.length || request.messages.some((m) => m.role !== "system"),
    {
      message: "messages must include at least one non-system message",
      path: ["messages"],
    },
  ) satisfies z.ZodType<CompletionRequest>;

export const conversationCreateSchema = z.looseObject({
  title: z.string().max(255).nullable().optional(),
}) satisfies z.ZodType<ConversationCreate>;

export const messageCreateSchema = z.looseObject({
  conversation_id: id,
  role,
  content: z.string().min(1).max(100_000),
  metadata: metadata.optional(),
}) satisfies z.ZodType<MessageCreate>;

export const memoryCreateSchema = z.looseObject({
  type: z.enum(["episodic", "semantic", "procedural", "preference"]),
  content: z.string().min(1).max(20_000),
  summary: z.string().max(1000).nullable().optional(),
  importance: z.number().min(0).max(1).optional(),
  source_message_id: id.nullable().optional(),
}) satisfies z.ZodType<MemoryCreate>;

export const knowledgeSearchSchema = z.looseObject({
  query: z.string().trim().min(1).max(2000),
  top_k: z.number().int().min(1).max(50).optional(),
  mode: searchMode.optional(),
  filters: searchFilters.optional(),
  recency_weight: z.number().min(0).max(1).optional(),
  max_context_tokens: z.number().int().min(100).max(200_000).optional(),
}) satisfies z.ZodType<KnowledgeSearchRequest>;

const chunking = z.looseObject({
  chunk_size: z.number().int().min(32).max(8192).optional(),
  chunk_overlap: z.number().int().min(0).max(2048).optional(),
  separators: z.array(z.string().min(1).max(16)).min(1).max(20).optional(),
});

export const documentCreateSchema = z.looseObject({
  title: z.string().min(1).max(512),
  content: z.string().trim().min(1),
  source: z.string().max(2048).optional(),
  mime_type: z.enum(["text/plain", "text/markdown"]).optional(),
  metadata: metadata.optional(),
  chunking: chunking.optional(),
}) satisfies z.ZodType<DocumentCreate>;

export const ingestOptionsSchema = z.looseObject({
  title: z.string().min(1).max(512).optional(),
  source: z.string().max(2048).optional(),
  metadata: metadata.optional(),
  chunking: chunking.optional(),
});

// --- Responses ------------------------------------------------------------------------------------

const timestamp = z.string();
const nullableString = z.string().nullable();

const citation = z.looseObject({
  index: z.number(),
  document_id: id,
  title: z.string(),
  label: z.string(),
  score: z.number(),
  snippet: z.string(),
});

const confidence = z.looseObject({ score: z.number(), level: z.string() });

export const completionResponseSchema = response<CompletionResponse>(
  z.looseObject({
    id,
    object: z.literal("cortex.completion"),
    created_at: timestamp,
    provider: z.string(),
    model: z.string(),
    output: z.string(),
    latency_ms: z.number(),
    tokens: z.looseObject({ prompt: z.number(), completion: z.number(), total: z.number() }),
    finish_reason: z.string(),
    routing_reason: z.string(),
    routing_mode: z.string(),
    cost_estimate: z.number(),
    attempts: z.array(
      z.looseObject({
        provider: z.string(),
        model: z.string(),
        success: z.boolean(),
        latency_ms: z.number(),
      }),
    ),
    conversation_id: id.nullable(),
    knowledge: z
      .looseObject({
        query_id: id,
        applied: z.boolean(),
        confidence,
        citations: z.array(citation),
        cited: z.array(z.number()),
      })
      .nullable(),
  }),
);

const conversation = z.looseObject({
  id,
  organization_id: id,
  title: nullableString,
  created_at: timestamp,
  updated_at: timestamp,
});

export const conversationSchema = response<Conversation>(conversation);

const message = z.looseObject({
  id,
  conversation_id: id,
  role: z.string(),
  content: z.string(),
  token_count: z.number(),
  created_at: timestamp,
});

export const messageSchema = response<Message>(message);

export const conversationDetailSchema = response<ConversationDetail>(
  conversation.extend({ message_count: z.number(), messages: z.array(message) }),
);

function page(item: z.ZodType) {
  return z.looseObject({
    items: z.array(item),
    total: z.number(),
    limit: z.number(),
    offset: z.number(),
  });
}

export const conversationListSchema = response<ConversationListResponse>(
  page(conversation.extend({ message_count: z.number(), session: z.string().nullable() })),
);

export const memorySchema = response<Memory>(
  z.looseObject({
    id,
    type: z.string(),
    summary: z.string(),
    content: z.string(),
    importance: z.number(),
    created_at: timestamp,
  }),
);

const knowledgeDocument = z.looseObject({
  id,
  title: z.string(),
  mime_type: z.string(),
  chunk_count: z.number(),
  token_count: z.number(),
  embedding_space: nullableString,
  ingestion: z.looseObject({ ingestion_ms: z.number() }),
  created_at: timestamp,
});

export const knowledgeDocumentSchema = response<KnowledgeDocument>(knowledgeDocument);
export const documentListSchema = response<DocumentListResponse>(page(knowledgeDocument));

export const knowledgeSearchResponseSchema = response<KnowledgeSearchResponse>(
  z.looseObject({
    query_id: id,
    query: z.string(),
    mode: z.string(),
    results: z.array(
      z.looseObject({
        chunk_id: id,
        document_id: id,
        title: z.string(),
        content: z.string(),
        score: z.number(),
        citation: z.number().nullable(),
      }),
    ),
    context: z.looseObject({
      text: z.string(),
      token_count: z.number(),
      citations: z.array(citation),
      confidence,
    }),
    metrics: z.looseObject({ latency_ms: z.number() }),
  }),
);

export const modelListSchema = response<ModelListResponse>(
  z.looseObject({
    models: z.array(
      z.looseObject({
        id: z.string(),
        provider: z.string(),
        model: z.string(),
        available: z.boolean(),
        allowed: z.boolean(),
      }),
    ),
  }),
);
