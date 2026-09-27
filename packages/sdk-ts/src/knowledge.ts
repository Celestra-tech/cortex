import type {
  KnowledgeMetricsResponse,
  KnowledgeQueryDetail,
  KnowledgeQueryListResponse,
  KnowledgeQueryLog,
  KnowledgeSearchRequest,
  KnowledgeSearchResponse,
} from "@celestra/cortex-types";

import type { RequestOptions } from "./http";
import { APIResource, type PageParams, segment } from "./resource";
import { knowledgeSearchResponseSchema, knowledgeSearchSchema, validateInput } from "./types";

/** Hybrid retrieval over ingested documents, with citation-numbered context. */
export class Knowledge extends APIResource {
  /**
   * Accepts a query string or full options. `context.text` is ready to drop
   * into a system prompt; `context.citations` explains each `[n]`.
   */
  async search(
    params: KnowledgeSearchRequest | string,
    options?: RequestOptions,
  ): Promise<KnowledgeSearchResponse> {
    const body = typeof params === "string" ? { query: params } : params;
    validateInput("knowledge.search", knowledgeSearchSchema, body);
    return this.transport.request({
      operation: "knowledge.search",
      method: "POST",
      path: "/v1/knowledge/search",
      json: body,
      // Read-only despite POST, so safe to retry after timeouts and 5xx.
      idempotent: true,
      schema: knowledgeSearchResponseSchema,
      options,
    });
  }

  /** Corpus size, ingestion and retrieval latency, and retrieval quality. */
  metrics(
    params: { windowHours?: number } = {},
    options?: RequestOptions,
  ): Promise<KnowledgeMetricsResponse> {
    return this.transport.request({
      operation: "knowledge.metrics",
      path: "/v1/knowledge/metrics",
      query: { window_hours: params.windowHours },
      options,
    });
  }

  /** Logged retrievals, newest first. */
  listQueries(
    params: PageParams = {},
    options?: RequestOptions,
  ): Promise<KnowledgeQueryListResponse> {
    return this.transport.request({
      operation: "knowledge.listQueries",
      path: "/v1/knowledge/queries",
      query: { limit: params.limit, offset: params.offset },
      options,
    });
  }

  iterQueries(params: PageParams = {}): AsyncGenerator<KnowledgeQueryLog> {
    return this.paginate((page) => this.listQueries(page), params);
  }

  /** One retrieval with its ranked results, for replaying citations. */
  getQuery(id: string, options?: RequestOptions): Promise<KnowledgeQueryDetail> {
    return this.transport.request({
      operation: "knowledge.getQuery",
      path: `/v1/knowledge/queries/${segment(id)}`,
      options,
    });
  }
}
