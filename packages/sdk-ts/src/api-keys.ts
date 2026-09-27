import type {
  ApiKey,
  ApiKeyCreate,
  ApiKeyListResponse,
  ApiKeyRotate,
  ApiKeyWithSecret,
} from "@celestra/cortex-types";

import type { RequestOptions } from "./http";
import { APIResource, segment } from "./resource";

/**
 * The organization's API keys. Requires an admin key.
 *
 * Secrets are returned only by `create` and `rotate`. Those calls are not
 * retried automatically: a retry after a lost response would mint a second key.
 */
export class ApiKeys extends APIResource {
  /** Newest first. Secrets are never listed. */
  list(
    params: { includeRevoked?: boolean } = {},
    options?: RequestOptions,
  ): Promise<ApiKeyListResponse> {
    return this.transport.request({
      operation: "apiKeys.list",
      path: "/v1/api-keys",
      query: { include_revoked: params.includeRevoked },
      options,
    });
  }

  get(id: string, options?: RequestOptions): Promise<ApiKey> {
    return this.transport.request({
      operation: "apiKeys.get",
      path: `/v1/api-keys/${segment(id)}`,
      options,
    });
  }

  /** Mints a key. Store `secret` now; it cannot be retrieved again. */
  create(params: ApiKeyCreate, options?: RequestOptions): Promise<ApiKeyWithSecret> {
    return this.transport.request({
      operation: "apiKeys.create",
      method: "POST",
      path: "/v1/api-keys",
      json: params,
      options: { maxRetries: 0, ...options },
    });
  }

  /** Replaces a key (same name and role). The old key lapses after the grace period. */
  rotate(
    id: string,
    params: ApiKeyRotate = {},
    options?: RequestOptions,
  ): Promise<ApiKeyWithSecret> {
    return this.transport.request({
      operation: "apiKeys.rotate",
      method: "POST",
      path: `/v1/api-keys/${segment(id)}/rotate`,
      json: params,
      options: { maxRetries: 0, ...options },
    });
  }

  /** Revokes immediately. The organization's last usable admin key cannot be revoked (409). */
  revoke(id: string, options?: RequestOptions): Promise<ApiKey> {
    return this.transport.request({
      operation: "apiKeys.revoke",
      method: "DELETE",
      path: `/v1/api-keys/${segment(id)}`,
      options,
    });
  }
}
