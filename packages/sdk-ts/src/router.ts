import type {
  ExecutionListResponse,
  ModelExecution,
  ModelListResponse,
} from "@celestra/cortex-types";

import type { RequestOptions } from "./http";
import { APIResource, type PageParams, segment } from "./resource";
import { modelListSchema } from "./types";

export interface ExecutionListParams extends PageParams {
  provider?: string;
  model?: string;
  success?: boolean;
  completionId?: string;
}

/** The model catalog and the append-only log of provider calls. */
export class Router extends APIResource {
  /** Every catalog model with live availability and this organization's policy verdict. */
  models(options?: RequestOptions): Promise<ModelListResponse> {
    return this.transport.request({
      operation: "router.models",
      path: "/v1/models",
      schema: modelListSchema,
      options,
    });
  }

  /** Newest first. One completion with fallbacks yields several executions. */
  listExecutions(
    params: ExecutionListParams = {},
    options?: RequestOptions,
  ): Promise<ExecutionListResponse> {
    return this.transport.request({
      operation: "router.listExecutions",
      path: "/v1/executions",
      query: {
        provider: params.provider,
        model: params.model,
        success: params.success,
        completion_id: params.completionId,
        limit: params.limit,
        offset: params.offset,
      },
      options,
    });
  }

  iterExecutions(params: ExecutionListParams = {}): AsyncGenerator<ModelExecution> {
    return this.paginate((page) => this.listExecutions({ ...params, ...page }), params);
  }

  getExecution(id: string, options?: RequestOptions): Promise<ModelExecution> {
    return this.transport.request({
      operation: "router.getExecution",
      path: `/v1/executions/${segment(id)}`,
      options,
    });
  }
}
