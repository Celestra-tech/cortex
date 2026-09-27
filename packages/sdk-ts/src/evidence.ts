import type {
  DecisionCreate,
  DecisionEvidence,
  DecisionListResponse,
  EvidenceGraph,
  EvidenceNode,
  EvidenceNodeDetail,
  EvidencePath,
} from "@celestra/cortex-types";

import type { RequestOptions } from "./http";
import { APIResource, type PageParams, segment } from "./resource";
import {
  decisionCreateSchema,
  decisionEvidenceSchema,
  decisionListSchema,
  evidenceGraphSchema,
  evidenceNodeDetailSchema,
  evidencePathSchema,
  validateInput,
} from "./types";

export interface EvidenceDepthParams {
  /** Hops to follow from the root, 1-10. Defaults to 4. */
  depth?: number;
}

export interface EvidencePathParams extends EvidenceDepthParams {
  source: string;
  target: string;
  /** Only follow edges downstream, from evidence toward decisions. */
  directed?: boolean;
}

/**
 * The Evidence Graph: every decision linked to the memories, messages,
 * documents, chunks, retrievals, and model runs that produced it.
 *
 * A decision is addressed by the id of what was decided. For completions
 * that is the completion id, so `evidence.decision(completion.id)` explains
 * any answer.
 */
export class Evidence extends APIResource {
  /** Recorded decisions, newest first. */
  listDecisions(params: PageParams = {}, options?: RequestOptions): Promise<DecisionListResponse> {
    return this.transport.request({
      operation: "evidence.listDecisions",
      path: "/v2/evidence",
      query: { limit: params.limit, offset: params.offset },
      schema: decisionListSchema,
      options,
    });
  }

  /** Every recorded decision, fetching pages lazily. */
  iterDecisions(params: PageParams = {}, options?: RequestOptions): AsyncGenerator<EvidenceNode> {
    return this.paginate((page) => this.listDecisions(page, options), params);
  }

  /** A decision and its evidence, strongest first, with contradictions. */
  decision(
    decisionId: string,
    params: EvidenceDepthParams = {},
    options?: RequestOptions,
  ): Promise<DecisionEvidence> {
    return this.transport.request({
      operation: "evidence.decision",
      path: `/v2/evidence/${segment(decisionId)}`,
      query: { depth: params.depth },
      schema: decisionEvidenceSchema,
      options,
    });
  }

  /** The decision's graph with signed depths and a provenance timeline. */
  graph(
    decisionId: string,
    params: EvidenceDepthParams = {},
    options?: RequestOptions,
  ): Promise<EvidenceGraph> {
    return this.transport.request({
      operation: "evidence.graph",
      path: `/v2/evidence/${segment(decisionId)}/graph`,
      query: { depth: params.depth },
      schema: evidenceGraphSchema,
      options,
    });
  }

  /** One node by its node id, its direct neighbors, and the decisions it fed. */
  node(
    nodeId: string,
    params: EvidenceDepthParams = {},
    options?: RequestOptions,
  ): Promise<EvidenceNodeDetail> {
    return this.transport.request({
      operation: "evidence.node",
      path: `/v2/evidence/node/${segment(nodeId)}`,
      query: { depth: params.depth },
      schema: evidenceNodeDetailSchema,
      options,
    });
  }

  /** The shortest chain of evidence between two nodes (node ids). */
  path(params: EvidencePathParams, options?: RequestOptions): Promise<EvidencePath> {
    return this.transport.request({
      operation: "evidence.path",
      path: "/v2/evidence/path",
      query: {
        source: params.source,
        target: params.target,
        directed: params.directed,
        depth: params.depth,
      },
      schema: evidencePathSchema,
      options,
    });
  }

  /**
   * Records a decision made outside Cortex with the evidence behind it.
   * Completions are recorded automatically. Not retried: a repeat with the
   * same `ref_id` is a conflict.
   */
  async recordDecision(
    params: DecisionCreate,
    options?: RequestOptions,
  ): Promise<DecisionEvidence> {
    validateInput("evidence.recordDecision", decisionCreateSchema, params);
    return this.transport.request({
      operation: "evidence.recordDecision",
      method: "POST",
      path: "/v2/evidence/decisions",
      json: params,
      schema: decisionEvidenceSchema,
      options: { maxRetries: 0, ...options },
    });
  }
}
