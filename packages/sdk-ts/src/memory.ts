import type {
  Conversation,
  ConversationContext,
  ConversationCreate,
  ConversationDetail,
  ConversationListResponse,
  ConversationSummary,
  Memory,
  MemoryCreate,
  MemorySearchResponse,
  MemoryType,
  Message,
  MessageCreate,
} from "@celestra/cortex-types";

import type { RequestOptions } from "./http";
import { APIResource, type PageParams, segment } from "./resource";
import {
  conversationCreateSchema,
  conversationDetailSchema,
  conversationListSchema,
  conversationSchema,
  memoryCreateSchema,
  memorySchema,
  messageCreateSchema,
  messageSchema,
  validateInput,
} from "./types";

export interface ContextParams {
  /** Max recent messages. Defaults to the whole hot session window. */
  limit?: number;
  /** Memory search text. Defaults to the latest user message. */
  query?: string;
  /** Long-term memories to include (0 disables). Defaults to 5. */
  memoryLimit?: number;
}

export interface MemorySearchParams {
  query?: string;
  types?: MemoryType[];
  minImportance?: number;
  limit?: number;
}

/** Conversations (hot Redis sessions backed by Postgres) and long-term memories. */
export class MemoryResource extends APIResource {
  async createConversation(
    params: ConversationCreate = {},
    options?: RequestOptions,
  ): Promise<Conversation> {
    validateInput("memory.createConversation", conversationCreateSchema, params);
    return this.transport.request({
      operation: "memory.createConversation",
      method: "POST",
      path: "/v1/conversations",
      json: params,
      schema: conversationSchema,
      options,
    });
  }

  /** Most recently active first, with message counts and hot-session state. */
  listConversations(
    params: PageParams = {},
    options?: RequestOptions,
  ): Promise<ConversationListResponse> {
    return this.transport.request({
      operation: "memory.listConversations",
      path: "/v1/conversations",
      query: { limit: params.limit, offset: params.offset },
      schema: conversationListSchema,
      options,
    });
  }

  /** Every conversation, fetched page by page. */
  iterConversations(params: PageParams = {}): AsyncGenerator<ConversationSummary> {
    return this.paginate((page) => this.listConversations(page), params);
  }

  getConversation(
    id: string,
    params: { messageLimit?: number } = {},
    options?: RequestOptions,
  ): Promise<ConversationDetail> {
    return this.transport.request({
      operation: "memory.getConversation",
      path: `/v1/conversations/${segment(id)}`,
      query: { message_limit: params.messageLimit },
      schema: conversationDetailSchema,
      options,
    });
  }

  async deleteConversation(id: string, options?: RequestOptions): Promise<void> {
    await this.transport.request({
      operation: "memory.deleteConversation",
      method: "DELETE",
      path: `/v1/conversations/${segment(id)}`,
      options,
    });
  }

  /** Appends to the conversation and its hot session. */
  async addMessage(params: MessageCreate, options?: RequestOptions): Promise<Message> {
    validateInput("memory.addMessage", messageCreateSchema, params);
    return this.transport.request({
      operation: "memory.addMessage",
      method: "POST",
      path: "/v1/messages",
      json: params,
      schema: messageSchema,
      options,
    });
  }

  /** Hot session window plus relevant long-term memories, ready for the next model turn. */
  getContext(
    conversationId: string,
    params: ContextParams = {},
    options?: RequestOptions,
  ): Promise<ConversationContext> {
    return this.transport.request({
      operation: "memory.getContext",
      path: `/v1/conversations/${segment(conversationId)}/context`,
      query: { limit: params.limit, query: params.query, memory_limit: params.memoryLimit },
      options,
    });
  }

  async storeMemory(params: MemoryCreate, options?: RequestOptions): Promise<Memory> {
    validateInput("memory.storeMemory", memoryCreateSchema, params);
    return this.transport.request({
      operation: "memory.storeMemory",
      method: "POST",
      path: "/v1/memories",
      json: params,
      schema: memorySchema,
      options,
    });
  }

  /** Ranked by relevance, importance, and recency. */
  searchMemories(
    params: MemorySearchParams = {},
    options?: RequestOptions,
  ): Promise<MemorySearchResponse> {
    return this.transport.request({
      operation: "memory.searchMemories",
      path: "/v1/memories",
      query: {
        query: params.query,
        type: params.types,
        min_importance: params.minImportance,
        limit: params.limit,
      },
      options,
    });
  }
}
